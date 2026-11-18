"""Cycle-0 system board (FREEZE-PLAYBOOK.md v2 section 1; CYCLE0-system-brief.md section 6): the 13 rows, rendered
fresh from one code tree on real run inputs, for the lander's panel (the builder never renders the panel board).

    python board.py <tree> <dev checkout holding output/run_* and assets/> <out dir> [--brand default|second]
                    [--rows 1,2,...] [--per-kind 2] [--no-surfaces]

<tree>: the commit under review (a clean worktree). <dev checkout>: read-only inputs (renders-use-real-run-data).

Round 8 (lane BOARD): render_surfaces.py runs through run_surfaces.py with board_speed's exact memo; every tile's
label carries its pairing class (board_evidence.pairing: STAND-IN / FORCED); rows 7, 9 and 12 carry production-encoder
clips (<out>/clips/*.mp4, listed in board.json "clips") with strips and curves read from the decoded clips
(board_motion.py); row 7 adds the caption reading record (row07_captions_cps.png/json); row 13 adds the motion-token
table (row13_motion_tokens.png/json); peer_feed_<feed>.png is a feed sheet per feed surface (--peers <dir>, else
STAND-IN slots); refusals_<brand>.png/json is the palette's refusal table; board.json "seconds" holds the time of every
row (printed as it goes).
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
    board.save(out.with_suffix(".png"), compress_level=1)   # lossless either way; level 1 saves the big rows faster
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


def fit_sample(text: str, font, track: float, room: float) -> str:
    """`text` as set at `font` with `track` px between letters, cut at a word (then a letter) and marked with an
    ellipsis ("...") when it is wider than `room`."""
    def width(s):
        return font.getlength(s) + track * max(0, len(s) - 1)
    if width(text) <= room:
        return text
    words = text.split(" ")
    while len(words) > 1 and width(" ".join(words) + "...") > room:
        words.pop()
    s = " ".join(words)
    while len(s) > 1 and width(s + "...") > room:
        s = s[:-1]
    return s + "..."


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
                # round 8 (TY-A-13, TY 1b): a sample never runs off the sheet; one that would is cut at a word and
                # marked "...", so every specimen line is seen whole at its true size
                lines = [fit_sample(text, font, track, w - 48) for text in lines]
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
    import board_extras as bx
    from PIL import Image, ImageOps

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


def row12_motion(out: Path, dev: Path, work: Path, manifest: dict) -> list[str]:
    """Motion (round 8, MO-1/MO-8/MO-9): every sequence drawn once, encoded once by the delivery encoder into
    <out>/clips, decoded once; strips at real frame times and their alpha / luma curves read from the decoded frames:
    the 16:9 end screen reveal, the 9:16 end-card reveal and its rise, the loop bridge with FOLLOW into the Reel's
    frame 0, the hook's hold and fade, the act dissolve; each named ease on real axes; the clips in the manifest."""
    import board_evidence as be
    import board_extras as bx
    import board_motion as bm

    from src import brand_system
    from src import design_cards as dc
    from src import end_card
    clips = out / "clips"
    made = []
    rec = manifest.setdefault("clips", {})

    def clip_rec(seq, row, what):
        rec[seq.clip["path"].name] = {"row": row, "what": what, "frames": seq.n, "seconds": round(seq.n / bm.FPS, 3),
                                      "drawn_encoded_decoded_s": getattr(seq, "seconds", None),
                                      "sha256": seq.clip["sha256"], "encoder": seq.clip["encoder"]}

    def fno(n):   # the frame number in a strip label ("... f012 t=...")
        import re
        return int(re.search(r"f(\d{3}) t=", n).group(1))
    end = bm.end_sequences(clips)
    for name, seq in end.items():
        cls, why = be.pairing(f"{'endscreen' if name.startswith('endscreen') else 'endcard'}__{seq.index}_{seq.topic}")
        clip_rec(seq, "9, 12", f"{name} of the catalogue's {seq.topic} case {seq.index}"
                 + (f" ({cls}: {why})" if cls != be.REAL else ""))
    # the 16:9 end screen: every 3rd frame, and its curves
    es = end["endscreen_169"]
    sheet(bm.strip(es, scale_h=270, label="end screen 16:9 "), out / "row12_motion_endscreen", tile_h=270, cols=6)
    t = [i / bm.FPS for i in range(es.n)]
    bm.plot({"frame mean": (t, es.luma), **{k: (t, v) for k, v in es.regions.items()}},
            f"16:9 end screen ({es.topic}, case {es.index}): luma of the decoded clip per frame", "luma (0-255)",
            (0, 255)).save(out / "row12_motion_endscreen_curves.png")
    bm.plot({"frame-to-frame MAD": (t, es.mad)}, "16:9 end screen: frame-to-frame mean absolute difference",
            "MAD (0-255)", (0, max(10.0, max(es.mad) * 1.1))).save(out / "row12_motion_endscreen_steps.png")
    made += ["row12_motion_endscreen", "row12_motion_endscreen_curves", "row12_motion_endscreen_steps"]
    # the 9:16 end card: every frame of its reveal, the share block's rise (measured) and the curves
    ec = end["endcard_916"]
    sheet(bm.strip(ec, scale_h=576, label="end card 9:16 "), out / "row12_motion_endcard916", tile_h=576, cols=8)
    t = [i / bm.FPS for i in range(ec.n)]
    bm.plot({"frame mean": (t, ec.luma), **{k: (t, v) for k, v in ec.regions.items()}},
            f"9:16 end card ({ec.topic}, case {ec.index}): luma of the decoded clip per frame", "luma (0-255)",
            (0, 255)).save(out / "row12_motion_endcard916_curves.png")
    made += ["row12_motion_endcard916", "row12_motion_endcard916_curves"]
    box = ec.stats.get("box")
    if box:
        b = tuple(int(round(v)) for v in box)
        crop = (max(0, b[0] - 20), max(0, b[1] - 40), min(1080, b[2] + 20), min(1920, b[3] + 40))
        rise = bm.rise_px(ec, b, ec.n - 1)
        tiles = [(f"{n} | offset {rise.get(fno(n), '-')} px", im)
                 for n, im in bm.strip(ec, crop=crop, scale_h=0, label="share block ") if 9 <= fno(n) <= 40]
        sheet(tiles, out / "row12_motion_rise", tile_h=None, cols=4)
        ks = sorted(rise)
        bm.plot({"share block offset (measured)": ([k / bm.FPS for k in ks], [rise[k] for k in ks])},
                "9:16 end card: the share block's rise, measured (row-profile correlation against the rest frame)",
                "px below rest", (0, 24), marks={"first word starts": 0.45}).save(out / "row12_motion_rise_curve.png")
        made += ["row12_motion_rise", "row12_motion_rise_curve"]
    # the loop bridge with FOLLOW, into the Reel's frame 0 (the clip's last frame)
    br = end["bridge_follow_916"]
    total = br.n - 1
    tiles = [(f"{'Reel frame 0' if fno(n) == total else f'k{fno(n):02d}'} t={fno(n) / bm.FPS:.3f}s "
              f"(frame {fno(n)} of {total})", im) for n, im in bm.strip(br, scale_h=576)]
    sheet(tiles, out / "row12_motion_bridge", tile_h=576, cols=8)
    t = [i / bm.FPS for i in range(br.n)]
    bm.plot({"frame mean": (t, br.luma), **{k: (t, v) for k, v in br.regions.items()}},
            f"loop bridge with FOLLOW ({br.topic}): luma per frame, the last point is the Reel's frame 0",
            "luma (0-255)", (0, 255)).save(out / "row12_motion_bridge_curves.png")
    bm.plot({"FOLLOW alpha (end_card.follow_alpha)": (t[:total], [end_card.follow_alpha(i, total) for i in range(total)]),
             "frame-to-frame MAD / 25": (t, [m / 25 for m in br.mad])},
            "loop bridge: FOLLOW's alpha as the renderer computes it, and the frame steps", "alpha (0-1)",
            (0, 1)).save(out / "row12_motion_bridge_alpha.png")
    made += ["row12_motion_bridge", "row12_motion_bridge_curves", "row12_motion_bridge_alpha"]
    # the hook: hold and fade
    hook = bm.hook_sequence(clips)
    if hook.get("seq"):
        hs = hook["seq"]
        cls, why = be.pairing(f"card__hook__{hook['ground']}__916")
        tag = f"{cls}: {why}" if cls != be.REAL else "own topic"
        clip_rec(hs, "12", f"hook card on the {hook['ground']} ground, claim {hook['claim']} ({tag})")
        tiles = [(f"{n} | {tag}" if k == 0 else n, im)
                 for k, (n, im) in enumerate(x for x in bm.strip(hs, scale_h=480, label="hook ")
                                             if fno(x[0]) % 2 == 0 or 30 <= fno(x[0]) <= 42)]
        sheet(tiles, out / "row12_motion_hook", tile_h=576, cols=8)
        t = [i / bm.FPS for i in range(hs.n)]
        bm.plot({"type alpha (_hook_alpha)": (t, [dc._hook_alpha(x) for x in t]),
                 "plate + wash alpha": (t, [dc._hook_alpha(x, dc.HOOK_GROUND_HOLD, dc.HOOK_GROUND_FADE) for x in t]),
                 "frame mean luma / 255": (t, [v / 255 for v in hs.luma])},
                f"hook card ({hook['ground']} ground, {tag}): the type's hold and fade, the ground's after it",
                "alpha (0-1)", (0, 1), marks={"HOOK_HOLD": dc.HOOK_HOLD, "HOOK_GROUND_HOLD": dc.HOOK_GROUND_HOLD,
                                               "CAPTION_CLEAR": dc.CAPTION_CLEAR}).save(out / "row12_motion_hook_curves.png")
        made += ["row12_motion_hook", "row12_motion_hook_curves"]
    for g, why in hook.get("refused", {}).items():
        manifest["labels"][f"card__hook__{g}__916 (row 12)"] = f"REFUSED: {why}"
    # the act dissolve
    diss = bm.act_dissolve(dev, clips, bx.ASWAN_DECK_RUN)
    ds = diss["seq"]
    clip_rec(ds, "12", f"act dissolve ({bm.act_fade_frames()} frames, xfade dissolve) between the run's stills "
             f"{', '.join(diss['stills'])}, held still (camera push left out)")
    sheet(bm.strip(ds, scale_h=576, label="act dissolve "), out / "row12_motion_dissolve", tile_h=576, cols=8)
    t = [i / bm.FPS for i in range(len(diss["mix"]))]
    bm.plot({"mix (least squares, a -> b)": (t, diss["mix"]), "share of pixels nearer b": (t, diss["nearer_b"])},
            f"act dissolve: {diss['fade_s']:.3f} s from {diss['hold_s']:.2f} s, measured on the decoded clip",
            "share (0-1)", (0, 1), marks={"start": diss["hold_s"], "end": diss["hold_s"] + diss["fade_s"]}
            ).save(out / "row12_motion_dissolve_curves.png")
    made += ["row12_motion_dissolve", "row12_motion_dissolve_curves"]
    # each named ease on real axes (row13_motion_tokens lists the durations that use each)
    curves = {name: ([k / 100 for k in range(101)], [fn(k / 100) for k in range(101)])
              for name, fn in brand_system.EASES.items()}
    bm.plot(curves, "brand_system.EASES over normalised progress (x = t / the element's duration; "
            "row13_motion_tokens lists each duration)", "eased value", (-0.1, 1.15),
            x_label="progress (t / the element's duration)").save(out / "row12_motion_eases.png")
    made.append("row12_motion_eases")
    return made


def uses(module: str, engine: str) -> str:
    """Whether src/<module>.py imports src/<engine>.py (round 8, TY-A-13: the table read "group B" from a stale
    hand-written note; it now states only what the tree shows)."""
    text = (Path("src") / f"{module}.py").read_text(encoding="utf-8")
    return (f"the {engine} engine (imports src/{engine}.py)" if f"from .{engine} import" in text or
            f"from . import {engine}" in text else f"own rule (does not import src/{engine}.py)")


def row13_table(out: Path, work: Path) -> list[str]:
    """The element-by-element table across surfaces, from the tokens and the modules as they are on this tree."""
    from PIL import Image, ImageDraw, ImageFont

    from src import brand, brand_system, end_card
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
         "see row 4 (the kickerless variant is a ruling)", f"same, {brand_system.type_px('kicker', 'wide'):.0f}px (WATCH NEXT)",
         "see rows 3 and 8"),
        ("line ends", f"never {len(brand_system.LINE_END_NEVER)} words, last resort {len(brand_system.LINE_END_LAST_RESORT)}",
         uses("youtube_thumbnail", "heading"), "-",
         f"covers: {uses('visual_engine', 'heading')}; cards: {uses('design_cards', 'heading')}"),
        ("shade", f"{t('plate.shade')}", "INK", "INK", "see rows 3, 8 and 11"),
        ("fail closed", "under the 5.5 gate on any letter: not drawn, recorded", "same", "same", "-"),
        ("eases", ", ".join(brand_system.EASES), "-", "see row13_motion_tokens", "see row13_motion_tokens"),
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
    # round 8 (MO-1): the motion tokens (durations, eases, overshoot) as this tree holds and draws them
    import board_motion as bm
    asses = {f"{c['topic']} {c['format']}": c["ass"] for c in caption_cases(work)}
    mrows = bm.motion_rows(asses)
    draw_table(mrows, (420, 560, 300, 760)).save(out / "row13_motion_tokens.png")
    (out / "row13_motion_tokens.json").write_text(json.dumps([dict(zip(mrows[0], r)) for r in mrows[1:]], indent=1,
                                                             default=str), encoding="utf-8", newline="\n")
    return ["row13_elements", "row13_motion_tokens"]


def caption_cases(work: Path) -> list[dict]:
    """Every caption case of the catalogue with its ASS as the composer builds it (no badge), its words and the
    composer's own reading-rate record (VideoComposer.last_reading_rate). Cached per process."""
    if "cases" in CAPTION_CACHE:
        return CAPTION_CACHE["cases"]
    import fixtures as fx

    from src import video_composer as vc
    out = []
    for case in fx.load("captions"):
        words = json.loads(Path(fx.path(case["words"])).read_text(encoding="utf-8"))
        wide = case.get("format") == "16:9"
        ass = work / f"cps_{case['topic']}_{'169' if wide else '916'}.ass"
        comp = vc.VideoComposer()
        comp.build_subtitles(words, ass, emphasis=case.get("emphasis_words") or None,
                             **({"width": 1920, "height": 1080} if wide else {}))
        out.append({"topic": case["topic"], "format": case.get("format"), "words": words,
                    "ass": ass.read_text(encoding="utf-8"), "record": getattr(comp, "last_reading_rate", None)})
    CAPTION_CACHE["cases"] = out
    return out


CAPTION_CACHE: dict = {}


def row7_cps(out: Path, work: Path) -> str:
    """The caption reading record (round 8, MO-1): per case and burst, on-screen seconds, characters, shown CPS, the
    spoken window and the word it ends on (never-end words flagged), beside the composer's own record."""
    import board_evidence as be
    rows = [("case", "burst", "on screen (s)", "chars", "shown CPS", "spoken window (s)", "ends on", "text")]
    record = {}
    for c in caption_cases(work):
        key = f"{c['topic']} {c['format']}"
        r = be.caption_rates(c["words"], c["ass"], c["record"])
        record[key] = r
        for k, b in enumerate(r["bursts"]):
            rows.append((key if k == 0 else "", f"{k + 1} @ {b['show']:.2f}s", f"{b['on_screen_s']:.2f}", b["chars"],
                         f"{b['shown_cps']}" + (" OVER 17" if (b["shown_cps"] or 0) > 17 else ""),
                         f"{b['spoken_window_s']:.2f}", b["ends_on"] + (" NEVER-END" if b["never_end"] else ""),
                         b["text"]))
        rec = r["composer_record"] or {}
        rows.append(("", "composer record", "", "", f"max {r['max_shown_cps']} (over 17: {r['over_17']})", "",
                     f"never-end endings: {r['never_end_count']}",
                     f"VideoComposer.last_reading_rate: bursts {rec.get('bursts')}, over_max {rec.get('over_max')}, "
                     f"worst {rec.get('worst')}"))
    draw_table(rows, (330, 170, 130, 80, 170, 170, 220, 900)).save(out / "row07_captions_cps.png")
    (out / "row07_captions_cps.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8",
                                                 newline="\n")
    return "row07_captions_cps"


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
    from src import brand
    from src import brand_system as bs
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
        return "#{:02X}{:02X}{:02X}".format(*tuple(rgb))
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
    out.append(("spacing", *per(lambda c: ", ".join(f"{k} {v}" for k, v in bs.SPACING[c].items())), ("safe boxes: "
                "src/safe_zones.py")))
    return out


def peer_feeds(out: Path, dev: Path, work: Path, peers: Path | None, manifest: dict) -> list[str]:
    """Round 8 (PL-E1): a feed sheet per feed surface at real pixel size (board_evidence.FEEDS), each of the heroes'
    tiles among 8 peer slots: the Reel cover and the carousel cover in the Instagram grid, the carousel cover in the
    Instagram feed, the YouTube thumbnail on home and in the suggested column, the Facebook gallery's first slide in
    the Facebook feed."""
    import board_evidence as be
    import board_extras as bx
    from PIL import Image
    surf = {p.stem: p for p in sorted((out / "surfaces").glob("*.png"))}

    def load(pred):
        return [(n, Image.open(p).convert("RGB")) for n, p in surf.items() if pred(n)]

    def hero(n):
        return any(h in n for h in HEROES)
    reels = load(lambda n: n.startswith("cover__") and n.endswith("__reel"))
    youtube = load(lambda n: n.startswith("cover__") and n.endswith("__youtube"))
    car1 = load(lambda n: n.startswith("social__") and n.endswith("__carousel_carousel_slide_01"))
    fb1 = load(lambda n: n.startswith("social__") and n.endswith("__fb_carousel_slide_01"))
    extra_c = bx._cache.get("covers") or []
    deck = (bx._cache.get("deck") or [])[:1]
    extra_reels = [(c["name"] + "__reel", c["reel"]) for c in extra_c]
    extra_yt = [(c["name"] + "__youtube", c["youtube"]) for c in extra_c]
    extra_car = [(i["name"], i["image"]) for i in deck]
    plan = {"ig_grid": ([x for x in reels + car1 if hero(x[0])] + extra_reels, reels + car1),
            "ig_feed": ([x for x in car1 if hero(x[0])] + extra_car, car1 + reels),
            "yt_home": ([x for x in youtube if hero(x[0])] + extra_yt, youtube),
            "yt_suggested": ([x for x in youtube if hero(x[0])] + extra_yt, youtube),
            "fb_feed": ([x for x in fb1 if hero(x[0])], fb1)}
    made = []
    for feed, (ours, pool) in plan.items():
        ours = [(be.label(n), im) for n, im in ours]
        tiles, note = be.peer_feed(feed, ours, pool, peers)
        if not tiles:
            continue
        name = f"peer_feed_{feed}"
        sheet(tiles, out / name, tile_h=None, cols=2 if feed != "yt_suggested" else 4)
        manifest["sheets"][name] = [n for n, _ in tiles]
        manifest.setdefault("peers", {})[feed] = note
        made.append(name)
    return made


def refusal_rows(out: Path, manifest: dict) -> list[tuple[str, str, str]]:
    """The elements this board saw not drawn (a masthead or end-card line failing closed, a FOLLOW skipped), beside
    the surfaces refused or raised (board_evidence.refusals reads those from surfaces.json and the manifest)."""
    import board_motion as bm
    rows = []
    p = out / "row02_masthead.json"
    if p.is_file():
        for k, rec in json.loads(p.read_text(encoding="utf-8")).items():
            if rec.get("shown") is False:
                rows.append((f"row 2 {k}", "element not drawn (masthead)", str(rec.get("reason", ""))[:300]))
    for name, seq in (bm._cache.get("end") or {}).items():
        st = getattr(seq, "stats", {}) or {}
        if (st.get("masthead") or {}).get("shown") is False:
            rows.append((f"{name} ({seq.topic})", "element not drawn (masthead)", str(st["masthead"].get("reason"))[:300]))
        for line, why in (st.get("refused_lines") or {}).items():
            rows.append((f"{name} ({seq.topic})", f"element not drawn ({line} line)", why[:300]))
        if st.get("follow_skipped"):
            rows.append((f"{name} ({seq.topic})", "element not drawn (FOLLOW)", str(st["follow_skipped"])[:300]))
    for item in bx_captions():
        rec = item.get("record") or {}
        if rec.get("timing_defects"):
            rows.append((item["name"], "caption timing defect", json.dumps(rec["timing_defects"][0])[:300]))
    return rows


def bx_captions() -> list[dict]:
    import board_extras as bx
    return bx._cache.get("captions") or []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tree", type=Path)
    ap.add_argument("dev", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--brand", choices=("default", "second"), default="default")
    ap.add_argument("--rows", default="")
    ap.add_argument("--per-kind", type=int, default=2)
    ap.add_argument("--no-surfaces", action="store_true", help="reuse <out>/surfaces (builder iteration only)")
    ap.add_argument("--peers", type=Path, help="peer posts for the feed sheets: <dir>/<feed>/*.jpg, feed one of "
                    "ig_grid, ig_feed, yt_home, yt_suggested, fb_feed (none: the slots are the channel's other runs, "
                    "labelled STAND-IN)")
    a = ap.parse_args()
    tree, dev, out = a.tree.resolve(), a.dev.resolve(), a.out.resolve()
    here = Path(__file__).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    rows = {int(r) for r in a.rows.split(",") if r} or set(range(1, 14))
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))
    os.chdir(tree)
    sys.path.insert(0, str(tree))
    sys.path.insert(0, str(tree / FIX))
    sys.path.insert(0, str(here))
    import board_speed
    board_speed.install()   # exact: the end card's ground built once per frame inside its line gate (board_speed.py)
    src_hash = hashlib.sha256()
    for f in sorted((tree / "src").glob("*.py")):
        src_hash.update(f.name.encode() + f.read_bytes().replace(bytes([13, 10]), bytes([10])))
    manifest = {"src_sha256": src_hash.hexdigest()[:16], "brand": a.brand, "rows": {}, "heroes": HEROES, "seconds": {}}
    t_all = time.perf_counter()

    def took(what: str, t0: float) -> None:
        manifest["seconds"][what] = round(time.perf_counter() - t0, 1)
        print(f"board: {what}: {manifest['seconds'][what]:.0f}s", file=sys.stderr, flush=True)
    if rows & set(ROWS) and not a.no_surfaces:
        t0 = time.perf_counter()
        script = tree / "docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py"
        cmd = [sys.executable, str(here / "run_surfaces.py"), str(script), str(tree), str(dev), str(out / "surfaces.json"),
               "--keep", str(out / "surfaces"), "--per-kind", str(a.per_kind)] + (
            ["--brand", "second"] if a.brand == "second" else [])
        subprocess.run(cmd, check=True)
        took("surfaces (render_surfaces.py)", t0)
    from src import brand
    if a.brand == "second":   # rows 1, 2, 12, 13 in QF-35a's test brand too
        sys.path.insert(0, str(tree / "docs/reviews/quality-fixes-2026-11/QF-35a"))
        import render_surfaces
        brand.configure(render_surfaces.second_brand(brand), validate=False)   # round 8: evidence (the corpus refuses it)
    import board_evidence as be
    import board_extras as bx
    import board_motion as bm
    work = Path(tempfile.mkdtemp(prefix="board-"))
    clips = out / "clips"
    manifest["labels"], manifest["errors"], manifest["sheets"] = {}, {}, {}
    manifest.setdefault("clips", {})

    def extra(name: str, fn, default=None):
        """One extra render: a failure is recorded in the manifest (and printed), never hidden, and the board goes on."""
        try:
            return fn()
        except Exception:  # noqa: BLE001
            manifest["errors"][name] = traceback.format_exc()[-1500:]
            print(f"board: {name} failed:\n{manifest['errors'][name]}", file=sys.stderr)
            return default

    if 1 in rows:
        t0 = time.perf_counter()
        manifest["rows"]["1"] = row1_specimen(out, dev)
        took("row 1", t0)
    if 2 in rows:
        t0 = time.perf_counter()
        manifest["rows"]["2"] = row2_masthead(out, dev, work)
        took("row 2", t0)
    from PIL import Image
    for r, (name, prefixes) in sorted(ROWS.items()):
        if r not in rows:
            continue
        t0 = time.perf_counter()
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
        # round 8 (AD E3, CD-20): every tile labelled through board_evidence.label, its pairing class first
        tiles = [(be.label(p.stem), Image.open(p).convert("RGB")) for p in heroes]
        sheets: dict[str, tuple[list, int | None, int]] = {}

        def add(item: dict, tiles=tiles) -> None:
            if item.get("image") is None:
                return
            tiles.append((be.label(item["name"], item["label"]), item["image"]))
            manifest["labels"][item["name"]] = be.label(item["name"], item["label"])
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
                [(f"{n} | {what}", cim) for n, im in reels for what, cim in bx.grid_crops(im)], None, 4)
        if r == 4:   # feed sizes beside the full frame
            full = list(tiles)
            tiles += [(f"{n} | @246 (4x)", im.resize((246, 138), Image.LANCZOS).resize((984, 552), Image.NEAREST))
                      for n, im in full]
            tiles += [(f"{n} | @168 (4x)", im.resize((168, 94), Image.LANCZOS).resize((672, 376), Image.NEAREST))
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
                    feed.append((be.label(n, what), v))
            sheets["row05_carousel_feed"] = (feed, None, 4)
        if r == 6:
            over = []
            sizes = {}
            sj = out / "surfaces.json"
            if sj.is_file():   # round 8 (AD E1): the Story's one title size, as the harness passed it
                sizes = json.loads(sj.read_text(encoding="utf-8")).get("story_title_px") or {}
            for n, im in tiles:
                o, box = bx.story_overlay(im)
                px = sizes.get(n.split(" | ")[0])
                over.append((f"{n} | title_px {px} (multi_format.story_title_px over the Story) | Story safe box "
                             f"{box} (safe_zones.safe_box('story'))", o))
            sheets["row06_story_safe_box"] = (over, 960, 4)
        if r in (7, 11):
            items = extra("caption encodes", lambda: bx.caption_frames(tree, work, clips), [])
            for item in items:
                if item["badge"] == (r == 11):
                    add(item)
                    manifest["clips"][item["clip"]["path"].name] = {
                        "row": str(r), "what": be.label(item["name"], item["label"]), "frames": item["seq"].n,
                        "seconds": round(item["seq"].n / bm.FPS, 3), "sha256": item["clip"]["sha256"],
                        "encoder": item["clip"]["encoder"]}
            if r == 7:   # the burst changes, frame by frame (word ease, plate fade), and the reading record
                strips = []
                for item in items:
                    if item["badge"]:
                        continue
                    for n, im in bm.strip(item["seq"], scale_h=480 if item["format"] == "9:16" else 360,
                                          label=f"{item['name']} "):
                        strips.append((n, im))
                sheets["row07_captions_clip_frames"] = (strips, None, 6)
                cps = extra("caption reading record", lambda: row7_cps(out, work))
                if cps:
                    manifest["sheets"][cps] = ["row07_captions_cps.json"]
        if r == 8:
            for item in extra("hook frames", bx.hook_frames, []):
                if "refused" in item:
                    manifest["labels"][item["name"]] = "REFUSED: " + item["refused"]
                    continue
                add(item)
        if r == 9:   # round 8 (TY-A-13, PL): FOLLOW where it is held, and the end card at rest, from the row-12 clips
            end = extra("end sequences", lambda: bm.end_sequences(clips), {}) or {}
            br, ec = end.get("bridge_follow_916"), end.get("endcard_916")
            if br is not None:
                held = [i for i in sorted(br.kept) if i < br.n - 1 and br.stats.get("follow")]
                for i in held[len(held) // 2: len(held) // 2 + 1]:
                    add({"name": f"bridge_follow__{br.index}_{br.topic}__f{i:02d}", "image": br.kept[i],
                         "label": f"FOLLOW held: frame {i} of {br.n - 1} (t={i / bm.FPS:.3f}s), production encode"})
            if ec is not None:
                add({"name": f"endcard__{ec.index}_{ec.topic}__rest", "image": ec.kept[ec.n - 1],
                     "label": f"at rest: frame {ec.n - 1} (t={(ec.n - 1) / bm.FPS:.3f}s), production encode"})
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
        took(f"row {r}", t0)
    if rows & {3, 4, 5, 6}:
        t0 = time.perf_counter()
        manifest["rows"]["peer_feed"] = extra("peer feeds", lambda: peer_feeds(out, dev, work, a.peers, manifest), [])
        took("peer feeds", t0)
    if 12 in rows:
        t0 = time.perf_counter()
        manifest["rows"]["12"] = extra("row 12", lambda: row12_motion(out, dev, work, manifest), [])
        took("row 12", t0)
    if 13 in rows:
        t0 = time.perf_counter()
        manifest["rows"]["13"] = row13_table(out, work)
        took("row 13", t0)
    # round 8: the refusal table of this palette (surface, what, reason)
    sj = out / "surfaces.json"
    surfaces = json.loads(sj.read_text(encoding="utf-8")) if sj.is_file() and (rows & set(ROWS)) else None
    ref = be.refusals(surfaces, manifest, refusal_rows(out, manifest))
    draw_table([("surface", "what", "reason")] + ref, (620, 330, 1500)).save(out / f"refusals_{a.brand}.png")
    (out / f"refusals_{a.brand}.json").write_text(json.dumps([dict(zip(("surface", "what", "reason"), r)) for r in ref],
                                                             indent=1), encoding="utf-8", newline="\n")
    manifest["refusals"] = {"count": len(ref), "table": f"refusals_{a.brand}.png"}
    shutil.rmtree(work, ignore_errors=True)
    took("board total (this process)", t_all)
    (out / "board.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8", newline="\n")
    print("board:", out, manifest["src_sha256"])


if __name__ == "__main__":
    main()

