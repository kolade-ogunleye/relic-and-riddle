"""Cycle-0 system board (FREEZE-PLAYBOOK.md v2 section 1; CYCLE0-system-brief.md section 6): the 13 rows, rendered
fresh from one code tree on real run inputs, for the lander's panel (the builder never renders the panel board).

    python board.py <tree> <dev checkout holding output/run_* and assets/> <out dir> [--brand default|second]
                    [--rows 1,2,...] [--per-kind 2] [--no-surfaces]
                    [--workers N] [--timings <previous surfaces.json>] [--surface-jobs <file>]

--workers N (default 1: one process, as before; board tooling only, never a different pixel): render_surfaces.py's
job list (its --list-jobs) in N shards, each run through --jobs as its own process, longest first onto the least
loaded (each job's seconds in --timings, else <out>/surfaces.json, else per-kind defaults), merged into one
surfaces.json (the one-process schema and content; only `seconds` is the shards' own timing). Then the composed rows'
slow independent draws (row 2's end screens and end cards at REVEAL_S, the row 9 / 12 end sequences' draw and encode,
row 11's callout held frame) run in N-1 worker processes beside the board process, which takes each result up where
it would have drawn it (its refusals recorded there, in the same order). A tree whose render_surfaces.py has no
--list-jobs renders its surfaces in one process. --surface-jobs: only these jobs (a small board for a check).

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

Cycle 3 (lane BOARD, queue/BOARD-cycle3.md): board.json "tree" stamps the code (HEAD, the drawn paths' tree ids,
dirty paths, the full src hash, the board tools' hash) and "inputs" the union of every data file the board read
(board_inputs.py); <out>/INPUT_HASHES.json gives freeze_manifest.py each media file's input hash. Rows 9 and 12 encode
the end screen, end card and both loop bridges (with and without FOLLOW) on every board ground with a catalogue case
(--end-topics narrows them; the first ground keeps the unsuffixed row-12 names, the others add __<topic>); the rise
strip is cut from the share block measured on the rest frame. A refused YouTube thumbnail's probe takes another run
of the ground's own topic first (REAL) before a cross-run STAND-IN. Every row draws a REFUSED / RAISED tile for each
of its surfaces render_surfaces.py refused or failed. Row 13 is generated (board_table.py). --peers fills what a
partial peer dir holds. --portability <jobs> draws a lane's candidate in the default palette, the second palette and
the non-history copy set side by side (board_portability.py).

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


def hero_pick(pick, stem=lambda p: p.stem):
    """Per surface kind (the name's first part), the board's hero cases when the kind has any, else every case of the
    kind (so a kind no hero has, such as then-and-now or the callout, is still on its row)."""
    kinds: dict[str, list] = {}
    for p in pick:
        kinds.setdefault(stem(p).split("__")[0], []).append(p)
    out = []
    for ps in kinds.values():
        out += [p for p in ps if any(h in stem(p) for h in HEROES)] or ps
    return out


def on_row(r: int, prefixes: tuple[str, ...], stem: str) -> bool:
    """Whether render_surfaces.py's surface `stem` belongs on row `r` (the one filter for drawn and refused ones)."""
    if not stem.startswith(prefixes):
        return False
    if r == 4:
        return stem.endswith("youtube")
    if r == 3:
        return stem.endswith(("reel", "grid"))
    if r == 5:
        return "__carousel_" in stem or "__fb_" in stem
    if r == 6:
        return "__story_" in stem
    if r == 8:   # the hook's clip ends after its type has faded: its frames come from the hold (row 8's hook frames)
        return not stem.startswith("card__hook__")
    if r == 11:   # the callout's last frame is after the exit: its held frame is drawn by the board
        return (not stem.startswith("captions__") or "__badge" in stem) and not stem.startswith("callouts__")
    return True


def undrawn_tiles(r: int, prefixes: tuple[str, ...], pngs: set[str], surfaces: dict | None) -> list[tuple[str, object]]:
    """Cycle 3 (p3 BC row 4, TY backlog): a REFUSED tile, with its recorded reason, for every surface of this row
    that render_surfaces.py refused (and an ERROR tile for one that raised), picked like the drawn ones (hero
    cases first per kind), so a refusal is on its own row, not only in row 2 and the refusal table."""
    import board_evidence as be
    recs = (surfaces or {}).get("surfaces") or {}
    errs = (surfaces or {}).get("errors") or {}
    names = sorted({n for n, rec in recs.items() if isinstance(rec, dict) and "refused" in rec}
                   | {n for n in errs if "__" in n})
    names = [n for n in names if n not in pngs and on_row(r, prefixes, n)]
    # chosen among every case of the row, drawn or not, so a refused hero case shows where its drawn twin would
    keep = set(hero_pick(sorted(pngs | set(names)), stem=lambda n: n)) & set(names)
    out = []
    for n in names:
        if n not in keep:
            continue
        if n in recs and "refused" in recs[n]:
            rec = recs[n]
            why = f"{rec['refused']}" + (f" [{rec.get('class')}{' ' + rec['code'] if rec.get('code') else ''}]"
                                         if rec.get("class") else "")
            out.append((be.label(n, "REFUSED (render_surfaces.py)"), be.refusal_tile(n, why)))
        else:
            last = [ln for ln in str(errs[n]).strip().splitlines() if ln.strip()]
            out.append((be.label(n, "ERROR (render_surfaces.py)"),
                        be.refusal_tile(n, last[-1][:300] if last else "?", word="RAISED")))
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


SPECIMEN_FACE_WEIGHTS = {"display": None, "text": 600, "data": 600, "masthead": 700}   # row 1's weight per face


def specimen_weights() -> dict[str, object]:
    """The weight row 1's specimen draws per role (the kicker at 800, else its face's), for row 13's weight table."""
    from src import brand_system
    return {role: 800 if role == "kicker" else (SPECIMEN_FACE_WEIGHTS[brand_system.FACE[role]] or "regular")
            for role in brand_system.FACE}


def row1_specimen(out: Path, dev: Path) -> list[str]:
    """Every type role at its token size (true px), face, weight and tracking, per canvas: one sheet on INK and one
    over a real hero under the held plate tier."""
    import board_evidence as be
    from PIL import Image, ImageDraw

    from src import brand, brand_system
    from src.typography import get_font, plan_text
    hero = Image.open(dev / "assets" / "unfinished_obelisk_aswan" / "01_commons.jpg").convert("RGB")
    weights = SPECIMEN_FACE_WEIGHTS
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
                    p = be.guarded(f"row 1 specimen {canvas} {ground} {role}", plan_text, im,
                                   (24, round(base_y + k * (step or 0))), text, font, fill, "ls", track,
                                   target=brand_system.tok("contrast.place"))
                    if p is not None:   # round 8: a refused sample is in the refusal table, the sheet goes on
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
ROW2_GROUNDS = {"dark wieliczka": "wieliczka_salt_mine/01_commons.jpg", "busy amber": "amber_room_heist/01_commons.jpg",
                "bright aswan": "unfinished_obelisk_aswan/01_commons.jpg",
                "near-white antikythera": "antikythera_mechanism/01_commons.jpg"}
REVEAL_SURFACES = (((1920, 1080), "end screen 1920"), ((1080, 1920), "end card 9:16"))


def at_reveal(ground, share, stats):
    """The end screen's / card's frame at REVEAL_S: the dim and the masthead fully in (the record is the renderer's
    own, judged on the ground at rest); drawing on to the rest frame would cost the whole 10 s clip for a ~1 % push."""
    from src import end_card
    last = None
    for k, last in enumerate(end_card.frames(ground, share, stats)):
        if k >= round(REVEAL_S * end_card.FPS):
            break
    return last


def reveal_frame(hero_path: str, size: tuple[int, int], dev: str):
    """Row 2's end screen or end card (`size`) at REVEAL_S on one ground, exactly as _masthead_surfaces draws it (its
    hero, its run's end_card_share, the same guarded call): (frame or None, the renderer's stats). A worker process
    runs it under --workers (prefetch_compose); the one-process board draws it inline."""
    import board_evidence as be
    import board_extras as bx
    from PIL import Image, ImageOps

    from src.script_engine import end_card_share
    topic = Path(hero_path).parent.name
    run = GROUND_RUNS.get(topic, bx.ASWAN_DECK_RUN)
    payload = json.loads((Path(dev) / "output" / run / "verified_script.json").read_text(encoding="utf-8"))
    hero = Image.open(hero_path).convert("RGB")
    stats: dict = {}
    what = dict(REVEAL_SURFACES)[tuple(size)]
    last = be.guarded(f"row 2 {topic} {what}", at_reveal, ImageOps.fit(hero, tuple(size), Image.LANCZOS),
                      end_card_share(payload)[0], stats)
    return last, stats


# --workers (board tooling only; every output identical to the one-process board's): the composed rows' slow,
# independent draws run in worker processes, started before row 1 and taken up where the one-process board draws them
PREFETCH: dict = {}   # key -> Future of board_evidence.captured(...)
END_TOPICS: list[str] = []   # cycle 3: the grounds whose end sequences rows 9 and 12 encode (main: --end-topics)


def prefetched(key):
    """A prefetched draw's value; its refusals join board_evidence.REFUSED here, where the inline draw records them."""
    import board_evidence as be
    return be.replay(PREFETCH.pop(key))


def _worker_init(tree: str, dev: str, here: str, brand_name: str) -> None:
    """A compose worker set up as main() sets up the board process (environment, paths, the exact memo, the brand)."""
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=dev)
    os.chdir(tree)
    for p in (here, str(Path(tree) / FIX), tree):
        if p in sys.path:
            sys.path.remove(p)
    sys.path[0:0] = [here, str(Path(tree) / FIX), tree]
    import board_speed
    board_speed.install()
    import board_inputs   # cycle 3: this worker's reads join the board's input record (board_evidence.captured)
    board_inputs.install([dev, board_inputs.FRAME_CACHE], skip=[tempfile.gettempdir() + os.sep + "board-"])
    if brand_name == "second":
        sys.path.insert(0, str(Path(tree) / "docs/reviews/quality-fixes-2026-11/QF-35a"))
        import render_surfaces

        from src import brand
        brand.configure(render_surfaces.second_brand(brand), validate=False)


def prefetch_compose(pool, rows: set[int], dev: Path, work: Path, clips: Path) -> list[str]:
    """Submit the composed rows' slow independent draws (longest first, in the order the rows take them up): row 2's
    end screen and end card at REVEAL_S per ground, the row 9 / 12 end sequences (drawn and encoded; decoded in this
    process) and row 11's callout held frame. Each is a pure function of its inputs; nothing it writes is shared."""
    import board_evidence as be
    import board_extras as bx
    import board_motion as bm
    done = []

    def reveal(size, what):
        for g, rel in ROW2_GROUNDS.items():
            p = dev / "assets" / rel
            if p.is_file():
                PREFETCH[(str(p), size)] = pool.submit(be.captured, reveal_frame, str(p), size, str(dev))
                done.append(f"row 2 {g} {what}")

    def end(names):
        for topic in END_TOPICS:
            for name in bm.end_names(topic):
                if name in names:
                    bm.PREFETCH[(topic, name)] = pool.submit(be.captured, bm.draw_end, clips, topic, name)
                    done.append(f"end sequence {name} ({topic})")
    if rows & {9, 12}:
        for topic in END_TOPICS:   # the fixture frames extracted (cached) here once, before the workers read them
            bm._end_jobs(topic)
    # the order (the pool takes them first in, first out): row 2's end screens (~470 s each), the 16:9 end sequences
    # (~800 s, the longest), row 2's end cards (~100 s), the other end sequences and the callout
    if 2 in rows:
        reveal(*REVEAL_SURFACES[0])
    if rows & {9, 12}:
        end(bm.END_NAMES[:1])
    if 2 in rows:
        reveal(*REVEAL_SURFACES[1])
    if rows & {9, 12}:
        end(bm.END_NAMES[1:])
    if 11 in rows:
        PREFETCH["callout held"] = pool.submit(be.captured, bx.callout_held, work)
        done.append("row 11 callout held frame")
    return done


def _masthead_surfaces(hero_path: Path, topic: str, dev: Path, work: Path):
    """Each surface that carries the masthead, drawn by its own renderer on `hero_path` (its real ground treatment:
    the carousel cover's grade and top-edge scrim, the YouTube thumbnail's photo and gradient, the end screen's and
    end card's dim and softening, through end_card.frames, at REVEAL_S), as (surface, image, the renderer's own masthead record, the masthead's crop
    box). The narration's last frame is the hero itself on the end screen and card. A surface whose renderer refuses
    (round 8: the tree's LayoutError, through board_evidence.guarded) yields (surface, None, {"refused": reason},
    None): recorded, and the other surfaces go on."""
    import board_evidence as be
    import board_extras as bx
    from PIL import Image, ImageOps

    from src.multi_format import MultiFormatGenerator, standalone_subject
    from src.script_engine import end_card_share, share_prompt
    run = GROUND_RUNS.get(topic, bx.ASWAN_DECK_RUN)
    payload = json.loads((dev / "output" / run / "verified_script.json").read_text(encoding="utf-8"))
    note = "" if topic in GROUND_RUNS else f" (copy: {run[4:]}, STAND-IN)"
    hero = Image.open(hero_path).convert("RGB")
    cards = payload["carousel_slides"]

    def refused(surface):
        return {"refused": be.REFUSED[-1][2] if be.REFUSED and be.REFUSED[-1][0] == surface else "refused"}
    surface = f"row 2 {topic} carousel 1440 cover slide"
    got = be.guarded(surface, MultiFormatGenerator()._render_carousel_slide,
                     "cover", hero_path, hero_path, cards[0], 0, len(cards), payload.get("poll") or {},
                     share_prompt(payload), False, subject=standalone_subject(payload))
    if got is None:
        yield "carousel 1440 cover slide" + note, None, refused(surface), None
    else:
        im, lay = got
        rec = dict(lay.meta.get("masthead") or {})
        box = rec.get("box") or (0, 0, 640, 140)
        yield "carousel 1440 cover slide" + note, im.convert("RGB"), rec, (0, 0, min(im.width, round(box[2]) + 140),
                                                                           min(im.height, round(box[3]) + 120))
    yt = bx.youtube(hero_path, payload, work / f"row2_{topic}_yt.jpg")
    if yt["image"] is None:
        yield "youtube 1920" + note, None, {"refused": be.REFUSED[-1][2] if be.REFUSED else "refused"}, None
    else:
        rec = dict(yt["masthead"] or {})
        box = rec.get("box") or (0, 0, 760, 170)
        yield "youtube 1920" + note, yt["image"], rec, (0, 0, round(box[2]) + 160, round(box[3]) + 120)

    for size, what in REVEAL_SURFACES:
        stats: dict = {}
        surface = f"row 2 {topic} {what}"
        if (str(hero_path), size) in PREFETCH:   # --workers: drawn by a worker process (prefetch_compose)
            last, stats = prefetched((str(hero_path), size))
        else:
            last = be.guarded(surface, at_reveal, ImageOps.fit(hero, size, Image.LANCZOS),
                              end_card_share(payload)[0], stats)
        what += f" (frame at {REVEAL_S}s)"
        if last is None:
            yield what + note, None, refused(surface), None
            continue
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
    import board_evidence as be
    from PIL import Image
    tiles, recs = [], {}
    for g, rel in ROW2_GROUNDS.items():
        p = dev / "assets" / rel
        if not p.is_file():
            continue
        t0 = time.perf_counter()
        for s, im, rec, crop in _masthead_surfaces(p, rel.split("/")[0], dev, work):
            print(f"board: row 2 {g} | {s}: {time.perf_counter() - t0:.0f}s", file=sys.stderr, flush=True)
            t0 = time.perf_counter()
            recs[f"{g} | {s}"] = {k: v for k, v in rec.items() if k != "box"} | (
                {"box": [round(v) for v in rec["box"]]} if rec.get("box") else {})
            if im is None:   # round 8: the renderer refused; the tile says so (and the refusal table lists it)
                tiles.append((f"{g} | {s} | REFUSED", be.refusal_tile(s, rec.get("refused", ""))))
                continue
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
        st = getattr(seq, "stats", None) or {}
        if "ladder" in st:   # cycle 3 (lane E): the composer's ladder as tried, and the renderer's own seam reading
            rec[seq.clip["path"].name].update(variant=st.get("variant"), ladder=st["ladder"],
                                              seam=bm.seam_reading(st), motion_source=st.get("motion_source"))

    for topic in END_TOPICS or [bm.END_TOPIC]:   # cycle 3: every board ground with a catalogue case
        made += end_motion(out, clips, topic, "" if topic == bm.END_TOPIC else f"__{topic}", clip_rec)
    # the hook: hold and fade
    hook = bm.hook_sequence(clips)
    if hook.get("seq"):
        hs = hook["seq"]
        cls, why = be.pairing(f"card__hook__{hook['ground']}__916")
        tag = f"{cls}: {why}" if cls != be.REAL else "own topic"
        clip_rec(hs, "12", f"hook card on the {hook['ground']} ground, claim {hook['claim']} ({tag})")
        tiles = [(f"{n} | {tag}" if k == 0 else n, im)
                 for k, (n, im) in enumerate(x for x in bm.strip(hs, scale_h=480, label="hook ")
                                             if frame_no(x[0]) % 2 == 0 or 30 <= frame_no(x[0]) <= 42)]
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
    diss = bm.act_dissolve(dev, clips, bx.ASWAN_DECK_RUN) or {"missing": "act dissolve not drawn"}
    if "missing" in diss:
        be.REFUSED.append(("act dissolve (row 12)", "not drawn (evidence)", diss["missing"][:400]))
    else:
        ds = diss["seq"]
        clip_rec(ds, "12", f"act dissolve ({diss['spec']['frames']} frames, xfade transition={diss['spec']['transition']}, "
                 f"{diss['spec']['source']}) between the run's stills "
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


def frame_no(n: str) -> int:
    """The frame number in a strip label ("... f012 t=...")."""
    import re
    return int(re.search(r"f(\d{3}) t=", n).group(1))


def end_motion(out: Path, clips: Path, topic: str, suffix: str, clip_rec) -> list[str]:
    """Row 12's end sequences of one ground (`suffix` "" for END_TOPIC keeps the pre-cycle-3 names): the 16:9 end
    screen, the 9:16 end card and its share block's rise (measured on the share block itself, cycle 3), the loop
    bridge with FOLLOW and the bridge without it, each with its curves, every one read from its production encode."""
    import board_evidence as be
    import board_motion as bm

    from src import end_card
    made = []
    end = bm.end_sequences(clips, topic)
    for name, seq in end.items():
        kind = "endscreen" if name.startswith("endscreen") else "endcard"
        cls, why = be.pairing(f"{kind}__{seq.index}_{seq.topic}")
        clip_rec(seq, "9, 12", f"{name} of the catalogue's {seq.topic} case {seq.index}"
                 + (f" ({cls}: {why})" if cls != be.REAL else "") + bridge_note(seq))
    where = f" ({topic})" if suffix else ""
    # the 16:9 end screen: every 3rd frame, and its curves
    es = end.get("endscreen_169")
    if es is not None:
        sheet(bm.strip(es, scale_h=270, label=f"end screen 16:9{where} "), out / f"row12_motion_endscreen{suffix}",
              tile_h=270, cols=6)
        t = [i / bm.FPS for i in range(es.n)]
        bm.plot({"frame mean": (t, es.luma), **{k: (t, v) for k, v in es.regions.items()}},
                f"16:9 end screen ({es.topic}, case {es.index}): luma of the decoded clip per frame", "luma (0-255)",
                (0, 255)).save(out / f"row12_motion_endscreen{suffix}_curves.png")
        bm.plot({"frame-to-frame MAD": (t, es.mad)}, f"16:9 end screen{where}: frame-to-frame mean absolute difference",
                "MAD (0-255)", (0, max(10.0, max(es.mad) * 1.1))).save(out / f"row12_motion_endscreen{suffix}_steps.png")
        made += [f"row12_motion_endscreen{suffix}", f"row12_motion_endscreen{suffix}_curves",
                 f"row12_motion_endscreen{suffix}_steps"]
    # the 9:16 end card: every kept frame of its reveal, the share block's rise (measured) and the curves
    ec = end.get("endcard_916")
    if ec is not None:
        sheet(bm.strip(ec, scale_h=576, label=f"end card 9:16{where} "), out / f"row12_motion_endcard916{suffix}",
              tile_h=576, cols=8)
        t = [i / bm.FPS for i in range(ec.n)]
        bm.plot({"frame mean": (t, ec.luma), **{k: (t, v) for k, v in ec.regions.items()}},
                f"9:16 end card ({ec.topic}, case {ec.index}): luma of the decoded clip per frame", "luma (0-255)",
                (0, 255)).save(out / f"row12_motion_endcard916{suffix}_curves.png")
        made += [f"row12_motion_endcard916{suffix}", f"row12_motion_endcard916{suffix}_curves"]
        made += rise_strip(out, ec, suffix, where)
    # the loop bridge with FOLLOW and without it, into the Reel's frame 0 (the clip's last frame)
    for name, what, sname in (("bridge_follow_916", "with FOLLOW", "row12_motion_bridge"),
                              ("bridge_916", "without FOLLOW", "row12_motion_bridge_nofollow")):
        br = end.get(name)
        if br is None:
            continue
        sname += suffix
        total = br.n - 1
        tiles = [(f"{'Reel frame 0' if frame_no(n) == total else f'k{frame_no(n):02d}'} t={frame_no(n) / bm.FPS:.3f}s "
                  f"(frame {frame_no(n)} of {total})", im) for n, im in bm.strip(br, scale_h=576)]
        # cycle 3 (lane E): every bridge tile says which rung of the composer's ladder shipped and the renderer's
        # own seam reading (never a decoded clip's)
        tiles = [(f"{n} | bridge {what}{where}" if k else f"{n} | bridge {what}{where}{bridge_note(br)}", im)
                 for k, (n, im) in enumerate(tiles)]
        sheet(tiles, out / sname, tile_h=576, cols=8)
        t = [i / bm.FPS for i in range(br.n)]
        bm.plot({"frame mean": (t, br.luma), **{k: (t, v) for k, v in br.regions.items()}},
                f"loop bridge {what} ({br.topic}): luma per frame, the last point is the Reel's frame 0",
                "luma (0-255)", (0, 255)).save(out / f"{sname}_curves.png")
        made += [sname, f"{sname}_curves"]
        if name == "bridge_follow_916":
            bm.plot({"FOLLOW alpha (end_card.follow_alpha)": (t[:total], [end_card.follow_alpha(i, total)
                                                                          for i in range(total)]),
                     "frame-to-frame MAD / 25 (decoded, motion only)": (t, [m / 25 for m in br.mad])},
                    f"loop bridge{where}: FOLLOW's alpha as the renderer computes it, and the frame steps "
                    f"(seam: renderer {bm.seam_reading(br.stats)['mad_last']})",
                    "alpha (0-1)", (0, 1)).save(out / f"{sname}_alpha.png")
            made.append(f"{sname}_alpha")
        else:
            bm.plot({"frame-to-frame MAD (decoded, motion only)": (t, br.mad)},
                    f"loop bridge {what}{where}: frame-to-frame mean absolute difference (seam: renderer "
                    f"{bm.seam_reading(br.stats)['mad_last']})", "MAD (0-255)", (0, max(10.0, max(br.mad) * 1.1))
                    ).save(out / f"{sname}_steps.png")
            made.append(f"{sname}_steps")
    return made


def bridge_note(seq) -> str:
    """A loop bridge's shipped rung of the composer's ladder (board_motion.shipped_bridge) and its seam as the
    renderer measures it (board_motion.seam_reading); "" for any other sequence."""
    import board_motion as bm
    st = getattr(seq, "stats", {}) or {}
    if "ladder" not in st:
        return ""
    seam = bm.seam_reading(st)
    failed = [f"{t['variant']} ({'; '.join(t['problems'])})" for t in st["ladder"] if not t["ok"]]
    return (f" | shipped variant: {st.get('variant')} (composer's ladder"
            + (f"; refused first: {', '.join(failed)}" if failed else "") + ")"
            + f" | seam {seam['mad_last']} of under {seam['gate_max']}, measured on {seam['measure']}"
            + f" | seam motion: {st.get('motion_source', '-')}")


RISE_TILES = 16   # the rise strip's most tiles (every kept frame of the rise when fewer)


def rise_strip(out: Path, ec, suffix: str, where: str) -> list[str]:
    """The 9:16 end card's share block rising into place, cropped from the share block itself as measured on the
    rest frame (board_motion.share_block_box; cycle 3: the layout box is the FOLLOW line's when the card has one, so
    the old crop was blank on a card whose FOLLOW comes late), with the offset measured per kept frame from the
    first frame that shows type to the frame it settles (+3), and the curve of every kept frame."""
    import board_motion as bm
    rest = ec.n - 1
    box, how = bm.share_block_box(ec, rest)
    if box is None:
        import board_evidence as be
        be.REFUSED.append((f"row 12 rise strip{where}", "not drawn (evidence)", how))
        return []
    crop = (max(0, box[0] - 20), max(0, box[1] - 40), min(ec.size[0], box[2] + 20), min(ec.size[1], box[3] + 40))
    rise = bm.rise_ink_px(ec, box, rest)
    ks = sorted(k for k in rise if k < rest)
    if ks:
        settle = next((k for k in ks if all(rise[j] == 0 for j in ks if j >= k)), ks[-1])
        window = [k for k in sorted(ec.kept) if ks[0] - 3 <= k <= settle + 3]
    else:
        window = [k for k in sorted(ec.kept) if 9 <= k <= 40]
    step = max(1, -(-len(window) // RISE_TILES))
    window = set(window[::step])
    tiles = [(f"{n} | offset {rise.get(frame_no(n), '-')} px", im)
             for n, im in bm.strip(ec, crop=crop, scale_h=0, label=f"share block{where} ") if frame_no(n) in window]
    if tiles:
        tiles[0] = (f"{tiles[0][0]} | crop {list(crop)}: {how}", tiles[0][1])
    sheet(tiles, out / f"row12_motion_rise{suffix}", tile_h=None, cols=4)
    bm.plot({"share block offset (measured)": ([k / bm.FPS for k in ks], [rise[k] for k in ks])},
            f"9:16 end card{where}: the share block's rise, measured (its type pixels matched to the rest frame's, every kept frame)",
            "px below rest", (0, 24), marks={"type first measured": ks[0] / bm.FPS} if ks else None
            ).save(out / f"row12_motion_rise{suffix}_curve.png")
    return [f"row12_motion_rise{suffix}", f"row12_motion_rise{suffix}_curve"]


def uses(module: str, engine: str) -> str:
    """Whether src/<module>.py imports src/<engine>.py (round 8, TY-A-13: the table read "group B" from a stale
    hand-written note; it now states only what the tree shows)."""
    text = (Path("src") / f"{module}.py").read_text(encoding="utf-8")
    return (f"the {engine} engine (imports src/{engine}.py)" if f"from .{engine} import" in text or
            f"from . import {engine}" in text else f"own rule (does not import src/{engine}.py)")


def row13_table(out: Path, work: Path) -> list[str]:
    """The element-by-element tables across surfaces, generated (cycle 3: never written by hand, so never stale):
    what this board's renderers recorded as drawn (board_table.drawn), what each surface's code reaches
    (board_table.code_rows), the weights the system holds and who reads them (board_table.weight_rows), and the
    per-canvas token table (element_rows); the motion table adds each end sequence's delivered length."""
    from PIL import Image, ImageDraw, ImageFont

    import board_motion as bm
    import board_table as bt
    ends = bm.all_end()
    parts = [
        ("Drawn on this board: each renderer's own masthead record per surface across its grounds (row 2 records, "
         "and the end sequences' stats of rows 9 / 12)", draw_table(bt.drawn(out, ends), (420, 190, 190, 230, 240, 230, 520))),
        ("From the code: each surface's entry point and what it reaches (an AST call graph over src/*.py; colours and "
         "tokens named in the surface's own module)", draw_table(bt.code_rows(), (560, 380, 330, 420, 760))),
        ("Weights: every <role>.weight value, the src modules that read it, and what row 1's specimen draws",
         draw_table(bt.weight_rows(specimen_weights()), (300, 120, 220, 560, 300))),
        ("Per element and canvas, from src/brand_system.py as this tree holds it (TYPE / TYPE_FALLBACK / TYPE_FLOOR / "
         "FACE / SPACING, gates, runtime tokens and SPEC):", draw_table(element_rows(), (250, 520, 520, 520, 590))),
    ]
    f = ImageFont.load_default(size=22)
    W = max(t.width for _c, t in parts)
    H = sum(40 + t.height for _c, t in parts)
    im = Image.new("RGB", (W, H), (255, 255, 255))
    y = 0
    for caption, t in parts:
        ImageDraw.Draw(im).text((10, y + 8), caption, fill=(0, 0, 0), font=f)
        im.paste(t, (0, y + 40))
        y += 40 + t.height
    im.save(out / "row13_elements.png")
    # round 8 (MO-1): the motion tokens (durations, eases, overshoot) as this tree holds and draws them; cycle 3
    # (MO r1): each end sequence's delivered length beside the constant that names it
    asses = {f"{c['topic']} {c['format']}": c["ass"] for c in caption_cases(work)}
    mrows = bm.motion_rows(asses) + bt.delivered_motion(ends)
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
        import board_evidence as be
        if be.guarded(f"captions {case['topic']} {case.get('format')} (ASS)", comp.build_subtitles, words, ass,
                      emphasis=case.get("emphasis_words") or None,
                      **({"width": 1920, "height": 1080} if wide else {})) is None and not ass.is_file():
            continue
        out.append({"topic": case["topic"], "format": case.get("format"), "words": words,
                    "ass": ass.read_text(encoding="utf-8"), "record": getattr(comp, "last_reading_rate", None)})
    CAPTION_CACHE["cases"] = out
    return out


CAPTION_CACHE: dict = {}


def row7_cps(out: Path, work: Path) -> str:
    """The caption reading record (round 8, MO-1): per case and burst, on-screen seconds, characters, shown CPS, the
    spoken window and the word it ends on (never-end words flagged), every line of a two-line burst in it and each
    line ending checked (round 8i, N3), beside the composer's own record."""
    import board_evidence as be
    rows = [("case", "burst", "on screen (s)", "chars", "shown CPS", "spoken window (s)", "ends on", "text")]
    record = {}
    for c in caption_cases(work):
        key = f"{c['topic']} {c['format']}"
        r = be.caption_rates(c["words"], c["ass"], c["record"])
        record[key] = r
        for k, b in enumerate(r["bursts"]):
            # round 8i (N3): the whole burst, its lines split by " / "; a line that ends on a never-end word is named
            inner = [(ln["ends_on"], "NEVER-END" if ln["never_end"] else "DANGLING") for ln in b["lines"]
                     if (ln["never_end"] or ln["dangling"]) and not ln["burst_end"]]
            rows.append((key if k == 0 else "", f"{k + 1} @ {b['show']:.2f}s", f"{b['on_screen_s']:.2f}", b["chars"],
                         f"{b['shown_cps']}" + (" OVER 17" if (b["shown_cps"] or 0) > 17 else ""),
                         f"{b['spoken_window_s']:.2f}", b["ends_on"] + (" NEVER-END" if b["never_end"] else "")
                         + "".join(f" (a line ends on {w}: {what})" for w, what in inner),
                         " / ".join(ln["text"] for ln in b["lines"])))
        rec = r["composer_record"] or {}
        rows.append(("", "composer record", "", "", f"max {r['max_shown_cps']} (over 17: {r['over_17']})", "",
                     f"never-end endings: {r['never_end_count']} burst, {r['line_break_never_end_count']} line break "
                     f"(NEVER_END_WORDS; {r['line_break_dangling_count']} by never_end_end)",
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
    extra_c = bx._cache.get("covers") or []
    deck = (bx._cache.get("deck") or [])[:1]
    extra_reels = [(c["name"] + "__reel", c["reel"]) for c in extra_c if c.get("reel") is not None]
    extra_yt = [(c["name"] + "__youtube", c["youtube"]) for c in extra_c if c.get("youtube") is not None]
    extra_car = [(i["name"], i["image"]) for i in deck]
    # cycle 3 (operator's peer research): the big history Pages post Reels with no designed cover, so their feed tile
    # is the video's first frame; our Reel's first frame (the hook card at frame 0) sits beside the designed cover
    try:   # bx.hook_frames records its own refusals; anything else is the feeds' error, the covers still shown
        hooks = bx.hook_frames()
    except Exception as e:  # noqa: BLE001
        manifest["errors"]["peer feeds: reel first frames"] = f"{type(e).__name__}: {e}"[:1500]
        hooks = []
    first = [(i["name"], i["image"]) for i in hooks if i.get("image") is not None and i["name"].endswith("__f00")]
    first_label = {n: f"REEL FIRST FRAME as the tile (no designed cover) | {be.label(n)}" for n, _ in first}
    # cycle 3 (operator evidence, peers/fb_feed/peer_01.png): our Facebook carousel goes out as a multi-photo post, and
    # the feed shows it as the collage of its first 5 photos (board_evidence.fb_collage), never slide 1 alone
    decks: dict[str, list] = {}
    for n in sorted(x for x in surf if x.startswith("social__") and "__fb_carousel_slide_" in x):
        decks.setdefault(n.split("__fb_carousel_slide_")[0], []).append(n)
    collages = [(f"{run}__fb_collage", be.fb_collage([Image.open(surf[n]).convert("RGB") for n in names]))
                for run, names in decks.items()]
    first_label.update({n: (f"FB MULTI-PHOTO COLLAGE as the feed shows it (first {min(len(decks[n[:-12]]), 5)} of "
                            f"{len(decks[n[:-12]])} Facebook slides) | {be.label(n)}") for n, _ in collages})
    plan = {"ig_grid": ([x for x in reels + car1 if hero(x[0])] + extra_reels + first, reels + car1),
            "ig_feed": ([x for x in car1 if hero(x[0])] + extra_car + first, car1 + reels),
            "yt_home": ([x for x in youtube if hero(x[0])] + extra_yt, youtube),
            "yt_suggested": ([x for x in youtube if hero(x[0])] + extra_yt, youtube),
            "fb_feed": (([x for x in collages if hero(x[0])] or collages) + first, collages),
            # cycle 3: the Page's Reels tab, whose tiles are each Reel's first frame: our designed Reel cover and our
            # Reel's first frame, each among the peers (--peers <dir>/fb_reels/*.png)
            "fb_reels": ([x for x in reels if hero(x[0])] + extra_reels + first, reels)}
    made = []
    for feed, (ours, pool) in plan.items():
        ours = [(first_label.get(n) or be.label(n), im) for n, im in ours]
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
    for _topic, name, seq in bm.all_end():
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


# --workers: render_surfaces.py's job list in shards, balanced by each job's seconds in a previous surfaces.json; a
# job with no previous time gets its kind's (the cycle-0 round-8i default board's per-kind mean, seconds), else 60 s
JOB_SECONDS = {"endcard": 410, "cards": 406, "route": 291, "map": 155, "callouts": 126, "social": 101, "scale": 91,
               "thennow": 55, "numbers": 47, "cross-section": 47, "timeline_dense": 21, "captions": 19, "timeline": 15,
               "cover": 8}
SHARD_ONLY_KEYS = ("surfaces", "errors", "seconds", "story_title_px",   # merged by union (a job's own records)
                   "retired", "rerouted", "bridge_ladder")   # cycle 3: retired frames not drawn, slides re-routed
SHARD_SAME_KEYS = ("src_sha256", "brand", "graphic_runs", "brand_validation")   # equal in every shard
SHARD_UNION_KEYS = ("input_files",)   # cycle 3: {path: sha256} read by a shard (board_inputs.py), merged by union


def shard_jobs(names: list[str], prior: dict[str, float], n: int) -> list[list[str]]:
    """`names` in `n` shards, longest first onto the least loaded (LPT); each shard keeps render_surfaces.py's own job
    order (a shard's run is the one-process run with the other jobs skipped)."""
    est = {k: float(prior.get(k) or JOB_SECONDS.get(k.split(" ")[0], 60)) for k in names}
    load, shards = [0.0] * n, [[] for _ in range(n)]
    for k in sorted(names, key=lambda k: (-est[k], names.index(k))):
        i = min(range(n), key=lambda j: (load[j], j))
        shards[i].append(k)
        load[i] += est[k]
    order = {k: i for i, k in enumerate(names)}
    return [sorted(s, key=order.get) for s in shards]


def merge_shards(parts: list[dict]) -> dict:
    """One surfaces.json from the shards' (the schema and content of a one-process run's, its `seconds` the shards'
    own): a key outside the known ones, a differing shared value or a surface in two shards is an error."""
    merged: dict = {}
    for part in parts:
        for k, v in part.items():
            if k in SHARD_ONLY_KEYS:
                dup = set(merged.setdefault(k, {})) & set(v)
                if dup:
                    raise RuntimeError(f"surfaces shards: {k} {sorted(dup)[:5]} in two shards")
                merged[k].update(v)
            elif k in SHARD_UNION_KEYS:   # cycle 3: each shard's reads; one file read with two contents is an error
                import board_inputs
                board_inputs.merge(merged.setdefault(k, {}), v)
            elif k in SHARD_SAME_KEYS:
                if k in merged and merged[k] != v:
                    raise RuntimeError(f"surfaces shards: {k} differs between shards ({merged[k]!r} / {v!r})")
                merged[k] = v
            elif k != "count":
                raise RuntimeError(f"surfaces shards: unknown key {k!r} (board.py merge_shards needs it)")
    merged["count"] = len(merged.get("surfaces", {}))
    return merged


def run_surfaces(here: Path, tree: Path, dev: Path, out: Path, per_kind: int, brand_name: str, workers: int,
                 timings: Path | None, subset: Path | None) -> dict:
    """render_surfaces.py through run_surfaces.py into <out>/surfaces and <out>/surfaces.json: one process, or
    `workers` shards (--jobs) merged into the same surfaces.json. Returns what ran (for board.json)."""
    script = tree / "docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py"
    base = [sys.executable, str(here / "run_surfaces.py"), str(script), str(tree), str(dev)]
    common = ["--per-kind", str(per_kind)] + (["--brand", "second"] if brand_name == "second" else [])
    keep = ["--keep", str(out / "surfaces")]
    if workers > 1 and "--list-jobs" not in script.read_text(encoding="utf-8"):
        print("board: this tree's render_surfaces.py has no --list-jobs: surfaces in one process", file=sys.stderr)
        workers = 1
    if workers <= 1:
        subprocess.run(base + [str(out / "surfaces.json")] + keep + common + (["--jobs", str(subset)] if subset else []),
                       check=True)
        return {"processes": 1}
    tmp = Path(tempfile.mkdtemp(prefix="board-shards-"))
    listing = tmp / "jobs.txt"
    subprocess.run(base + [str(tmp / "listing.json")] + common + (["--jobs", str(subset)] if subset else [])
                   + ["--list-jobs", str(listing)], check=True, stdout=subprocess.DEVNULL)
    names = listing.read_text(encoding="utf-8").splitlines()
    src = timings or (out / "surfaces.json")
    prior = {}
    if src.is_file():
        try:
            prior = json.loads(src.read_text(encoding="utf-8")).get("seconds") or {}
        except ValueError:
            prior = {}
    shards = [s for s in shard_jobs(names, prior, workers) if s]
    procs = []
    for k, jobs in enumerate(shards):
        (tmp / f"shard{k}.jobs").write_text("".join(f"{j}\n" for j in jobs), encoding="utf-8", newline="\n")
        log = open(tmp / f"shard{k}.log", "w", encoding="utf-8")
        cmd = base + [str(tmp / f"shard{k}.json")] + keep + common + ["--jobs", str(tmp / f"shard{k}.jobs")]
        procs.append((k, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log, time.perf_counter()))
    print(f"board: surfaces in {len(shards)} shards ({len(names)} jobs, times from "
          f"{src if prior else 'the per-kind defaults'}), logs in {tmp}", file=sys.stderr, flush=True)
    failed, took_s = [], {}
    for k, p, log, t0 in procs:
        p.wait()
        log.close()
        took_s[k] = round(time.perf_counter() - t0, 1)
        if p.returncode:
            failed.append(k)
            print(f"board: surfaces shard {k} failed ({p.returncode}):\n"
                  + (tmp / f"shard{k}.log").read_text(encoding="utf-8", errors="replace")[-3000:], file=sys.stderr)
    if failed:
        raise RuntimeError(f"surfaces shards {failed} failed (logs in {tmp})")
    merged = merge_shards([json.loads((tmp / f"shard{k}.json").read_text(encoding="utf-8")) for k in range(len(shards))])
    missing = [n for n in names if n not in merged.get("seconds", {})]
    if missing:
        raise RuntimeError(f"surfaces shards: jobs not run {missing[:5]} (logs in {tmp})")
    (out / "surfaces.json").write_text(json.dumps(merged, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    print(merged["count"], "surfaces,", len(merged.get("errors", {})), "errors,",
          round(sum(merged["seconds"].values())), "s of jobs in", len(shards), "shards")
    shutil.rmtree(tmp, ignore_errors=True)
    return {"processes": len(shards), "jobs": len(names), "balanced_by": str(src) if prior else "per-kind defaults",
            "shard_jobs_seconds": [round(sum(merged["seconds"].get(j) or 0 for j in s), 1) for s in shards],
            "shard_seconds": [took_s[k] for k in range(len(shards))]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tree", type=Path)
    ap.add_argument("dev", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--brand", choices=("default", "second"), default="default")
    ap.add_argument("--rows", default="")
    ap.add_argument("--per-kind", type=int, default=2)
    ap.add_argument("--no-surfaces", action="store_true", help="reuse <out>/surfaces (builder iteration only)")
    ap.add_argument("--workers", type=int, default=1, help="processes (1: one, as before): render_surfaces.py's jobs "
                    "in this many shards, and the composed rows' slow independent draws in this many minus one "
                    "workers beside the board process; every output is the one-process board's")
    ap.add_argument("--timings", type=Path, help="the surfaces.json whose per-job seconds balance the shards "
                    "(default: <out>/surfaces.json when there is one, else per-kind defaults)")
    ap.add_argument("--surface-jobs", type=Path, help="render only these render_surfaces.py jobs (one name per line, "
                    "as surfaces.json `seconds` keys): a small board for a check")
    ap.add_argument("--peers", type=Path, help="peer posts for the feed sheets: <dir>/<feed>/*.jpg, feed one of "
                    "ig_grid, ig_feed, yt_home, yt_suggested, fb_feed, fb_reels (none: the slots are the channel's other runs, "
                    "labelled STAND-IN; a feed folder that is missing or holds fewer than 8 posts is filled with "
                    "labelled STAND-INs)")
    ap.add_argument("--end-topics", default="", help="rows 9 / 12: only these board grounds' end sequences "
                    "(comma-separated; default every board ground with a catalogue end-card case)")
    ap.add_argument("--portability", type=Path, help="a lane's freeze candidate: a file of render_surfaces.py job "
                    "names (as --surface-jobs). Renders them in the default palette, the second palette and the "
                    "default palette on the non-history stress copy set (board_portability.py), side by side, into "
                    "<out>/portability; nothing else is drawn")
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
    if a.portability:   # cycle 3: the side-by-side portability board of one lane's candidate, nothing else
        import board_portability
        board_portability.main(here, tree, dev, out, a.portability.resolve(), a.workers)
        return
    import board_inputs   # cycle 3: the code stamp and every data file the board reads (MANIFEST provenance)
    board_inputs.install([dev, board_inputs.FRAME_CACHE], skip=[out])
    stamp = board_inputs.tree_stamp(tree, here)
    src_hash = hashlib.sha256()
    for f in sorted((tree / "src").glob("*.py")):
        src_hash.update(f.name.encode() + f.read_bytes().replace(bytes([13, 10]), bytes([10])))
    manifest = {"src_sha256": src_hash.hexdigest()[:16], "brand": a.brand, "rows": {}, "heroes": HEROES, "seconds": {},
                "tree": stamp}
    t_all = time.perf_counter()
    import board_motion as bm
    END_TOPICS[:] = [t for t in bm.end_topics(HEROES)
                     if not a.end_topics or t in {x.strip() for x in a.end_topics.split(",")}]
    manifest["end_topics"] = list(END_TOPICS)

    def took(what: str, t0: float) -> None:
        manifest["seconds"][what] = round(time.perf_counter() - t0, 1)
        print(f"board: {what}: {manifest['seconds'][what]:.0f}s", file=sys.stderr, flush=True)
    if rows & set(ROWS) and not a.no_surfaces:
        t0 = time.perf_counter()
        manifest["workers"] = {"surfaces": run_surfaces(here, tree, dev, out, a.per_kind, a.brand, a.workers,
                                                        a.timings.resolve() if a.timings else None,
                                                        a.surface_jobs.resolve() if a.surface_jobs else None)}
        took("surfaces (render_surfaces.py)", t0)
    from src import brand
    if a.brand == "second":   # rows 1, 2, 12, 13 in QF-35a's test brand too
        sys.path.insert(0, str(tree / "docs/reviews/quality-fixes-2026-11/QF-35a"))
        import render_surfaces
        brand.configure(render_surfaces.second_brand(brand), validate=False)   # round 8: evidence (the corpus refuses it)
    import board_evidence as be
    if a.brand == "second":   # the configure-time validation's own list, in this palette's refusal table
        try:
            from src.brand_system import validate_display
            for why in validate_display(brand.active()) or []:
                be.REFUSED.append(("brand (configure-time fit corpus)", "brand refused at configure", str(why)[:400]))
        except ImportError:
            pass
    import board_extras as bx
    import board_motion as bm
    work = Path(tempfile.mkdtemp(prefix="board-"))
    clips = out / "clips"
    manifest["labels"], manifest["errors"], manifest["sheets"] = {}, {}, {}
    manifest.setdefault("clips", {})
    pool = None
    if a.workers > 1 and rows & {2, 9, 11, 12}:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        n = max(1, a.workers - 1)   # the board process composes beside them: `workers` processes in all
        pool = ProcessPoolExecutor(n, mp_context=multiprocessing.get_context("spawn"), initializer=_worker_init,
                                   initargs=(str(tree), str(dev), str(here), a.brand))
        manifest.setdefault("workers", {})["compose"] = {"processes": n, "prefetched": prefetch_compose(
            pool, rows, dev, work, clips)}

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
    sj = out / "surfaces.json"
    surfaces_rec = json.loads(sj.read_text(encoding="utf-8")) if sj.is_file() else None
    for k in ("retired", "rerouted", "bridge_ladder"):   # cycle 3: what production delivers today, not as recorded
        if (surfaces_rec or {}).get(k):
            manifest[k] = surfaces_rec[k]
    for r, (name, prefixes) in sorted(ROWS.items()):
        if r not in rows:
            continue
        t0 = time.perf_counter()
        n_refused = len(be.REFUSED)
        pngs = sorted((out / "surfaces").glob("*.png"))
        retired = set((surfaces_rec or {}).get("retired") or {})   # cycle 3: a retired frame's old file is not shown
        pick = [p for p in pngs if on_row(r, prefixes, p.stem) and p.stem not in retired]
        heroes = hero_pick(pick)
        # round 8 (AD E3, CD-20): every tile labelled through board_evidence.label, its pairing class first
        ladder = (surfaces_rec or {}).get("bridge_ladder") or {}

        def shipped(stem: str) -> str:   # cycle 3 (lane E): a bridge surface says which rung of the ladder shipped
            rec = ladder.get(stem)
            if not rec:
                return ""
            seam = rec.get("seam") or {}
            return (f"shipped variant: {rec.get('variant')} (composer's ladder) | seam {seam.get('mad_last')} of under "
                    f"{seam.get('gate_max')} | seam motion: {rec.get('motion_source', '-')}")
        tiles = [(be.label(p.stem, shipped(p.stem)), Image.open(p).convert("RGB")) for p in heroes]
        sheets: dict[str, tuple[list, int | None, int]] = {}

        def add(item: dict, tiles=tiles) -> None:
            if item.get("image") is None:
                if item.get("refused"):   # round 8: the renderer refused this surface; its row says so
                    tiles.append((be.label(item["name"], "REFUSED"), be.refusal_tile(item["name"], item["refused"])))
                return
            tiles.append((be.label(item["name"], item["label"]), item["image"]))
            manifest["labels"][item["name"]] = be.label(item["name"], item["label"])
        if r in (3, 4):
            for c in extra("covers", lambda: bx.covers(dev, work, HEROES), []):
                if r == 3:
                    for k in ("reel", "grid"):
                        add({"name": c["name"] + f"__{k}", "label": c["label"], "image": c[k],
                             "refused": (c.get("refused") or {}).get(k)})
                else:
                    add({"name": c["name"] + "__youtube", "label": c["label"], "image": c["youtube"],
                         "refused": (c.get("refused") or {}).get("youtube")})
                    for item in c.get("standins") or []:   # round 8i (N4): the refused ground judged on passing copy
                        add(item)
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
                if isinstance(px, dict):   # cycle 3: the frame's own layout record (what it drew at)
                    size = (f"title_px {px.get('drawn')} (this frame's layout record; the Story's one size "
                            f"{px.get('story')})")
                else:                      # an older surfaces.json: the size the harness passed
                    size = f"title_px {px} (multi_format.story_title_px over the Story, as the harness passed it)"
                over.append((f"{n} | {size} | Story safe box {box} (safe_zones.safe_box('story'))", o))
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
        if r == 9:   # round 8 (TY-A-13, PL): FOLLOW where it is held, and the end card at rest, from the row-12 clips;
            # cycle 3: on every board ground with a catalogue case (END_TOPICS), and the end screen at rest
            for topic in END_TOPICS or [bm.END_TOPIC]:
                end = extra(f"end sequences ({topic})", lambda topic=topic: bm.end_sequences(clips, topic), {}) or {}
                br, ec = end.get("bridge_follow_916"), end.get("endcard_916")
                if br is not None:
                    held = [i for i in sorted(br.kept) if i < br.n - 1 and br.stats.get("follow")]
                    for i in held[len(held) // 2: len(held) // 2 + 1]:
                        add({"name": f"bridge_follow__{br.index}_{br.topic}__f{i:02d}", "image": br.kept[i],
                             "label": f"FOLLOW held: frame {i} of {br.n - 1} (t={i / bm.FPS:.3f}s), production encode"})
                if ec is not None:
                    add({"name": f"endcard__{ec.index}_{ec.topic}__rest", "image": ec.kept[ec.n - 1],
                         "label": f"at rest: frame {ec.n - 1} (t={(ec.n - 1) / bm.FPS:.3f}s), production encode"})
                es = end.get("endscreen_169")
                if es is not None:
                    add({"name": f"endscreen__{es.index}_{es.topic}__rest", "image": es.kept[es.n - 1],
                         "label": f"at rest: frame {es.n - 1} (t={(es.n - 1) / bm.FPS:.3f}s), production encode"})
        if r == 11:
            for item in extra("callout held frame", lambda: prefetched("callout held") if "callout held" in PREFETCH
                              else bx.callout_held(work), []):
                add(item)
            for item in extra("cover badges", lambda: bx.cover_badges(dev, work, HEROES), []):
                add(item)
        for surface, what, why in be.REFUSED[n_refused:]:   # round 8: a refusal on its row, never a gap or an abort
            if surface.startswith(("youtube ", "reel cover + grid ")):
                continue   # the covers' refusals are shown on their own rows (3 and 4) by add()
            tiles.append((f"{surface} | {what}", be.refusal_tile(surface, why)))
        # cycle 3: the surfaces render_surfaces.py refused (or that raised) on this row, each as its own tile
        tiles += undrawn_tiles(r, prefixes, {p.stem for p in pngs}, surfaces_rec)
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
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=True)
        PREFETCH.clear()
        bm.PREFETCH.clear()
    shutil.rmtree(work, ignore_errors=True)
    took("board total (this process)", t_all)
    # cycle 3 (AD r1 AD-EV-manifest): the inputs' union (render_surfaces.py's shards, this process, the workers) and
    # INPUT_HASHES.json, which freeze_manifest.py reads into each image's input_hash
    inputs: dict[str, str] = {}
    sj = out / "surfaces.json"
    if sj.is_file():
        board_inputs.merge(inputs, json.loads(sj.read_text(encoding="utf-8")).get("input_files") or {})
    board_inputs.merge(inputs, board_inputs.files())
    board_inputs.merge(inputs, be.WORKER_INPUTS)
    manifest["inputs"] = {"count": len(inputs), "sha256": board_inputs.union_sha256(inputs), "files": inputs,
                          "note": "every data file read under the dev checkout (and fixtures.frame's cache) by "
                                  "render_surfaces.py, this process and its workers; code is the tree stamp's"}
    hashes = board_inputs.input_hashes(out, stamp, a.brand, inputs)
    (out / "INPUT_HASHES.json").write_text(json.dumps(hashes, indent=1, sort_keys=True), encoding="utf-8",
                                           newline="\n")
    manifest["input_hashes"] = {"file": "INPUT_HASHES.json", "count": len(hashes)}
    (out / "board.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8", newline="\n")
    print("board:", out, manifest["src_sha256"])


if __name__ == "__main__":
    main()

