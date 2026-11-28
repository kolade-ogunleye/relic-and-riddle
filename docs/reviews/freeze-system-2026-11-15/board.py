"""Cycle-0 system board (FREEZE-PLAYBOOK.md v2 section 1; CYCLE0-system-brief.md section 6): the 13 rows, rendered
fresh from one code tree on real run inputs, for the lander's panel (the builder never renders the panel board).

    python board.py <tree> <dev checkout holding output/run_* and assets/> <out dir> [--brand default|second|second-colours]
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
Run it once per palette (default, --brand second: QF-35a's test brand, --brand second-colours: its colours with
today's faces) into one out dir each. One heavy run: it starts
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

Cycle 4 (lane BOARD, queue/BOARD-cycle4.md): --brand second-colours (PALETTES; board.json "palette" lists the roles it
moves); --workers N merges every render_surfaces.py record key (the omission ledger included: it crashed on
"omissions"); row 12 adds the then-and-now cases (both formats) and the photo callout as clips encoded by their own
renderers, each with a strip, curves and a timing table from the renderer's own schedule (row12_motion_thennow__*,
row12_motion_callouts__*); row 13 adds every social frame's masthead record by kind (the Facebook cover's variant,
the Facebook interiors drawn without one) and row13_fb_masthead (the regions); a surface drawn without a part is on
its row with a RECORDED OMISSION note on its label (render_surfaces.py "surface_omissions") and in the refusal table;
a stress ground's STAND-IN label says why from the dev checkout.

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


def save_case(out: Path, manifest: dict, name: str, image) -> None:
    """Cycle 5 (panel 6: 41 board stems had no full-size file, only their tile on the half-scale row): a case the
    board draws itself (never render_surfaces.py: the bright and near-white covers, the Aswan deck, the hook's held
    frames, the frames taken from the clips, the badges) is saved at full size as <out>/cases/<name>.png."""
    if (out / "surfaces" / f"{name}.png").is_file():
        return
    (out / "cases").mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(out / "cases" / f"{name}.png")
    manifest.setdefault("case_files", {})[name] = f"cases/{name}.png"


def case_files(out: Path, manifest: dict, names: list[str]) -> None:
    """board.json "case_files": every case of a row -> its own full-size file (surfaces/<name>.png from
    render_surfaces.py, else cases/<name>.png from the board's own draw), or None for a tile that is no picture (a
    refused or raised surface: its reason is in the refusal table)."""
    rec = manifest.setdefault("case_files", {})
    for n in names:
        if (out / "surfaces" / f"{n}.png").is_file():
            rec[n] = f"surfaces/{n}.png"
        else:
            rec.setdefault(n, None)


WIDE_CAPTIONS: tuple[str, ...] = ()   # the topics whose caption fixture is 16:9 (main sets it from the catalogue)


def clip_tier_name(stem: str) -> str:
    """A clip's name as the tier rule reads surfaces: <kind>_<916|169>__<case> -> <kind>__<case>__<916|169>."""
    head, _, rest = stem.partition("__")
    for tag in ("916", "169"):
        if head.endswith(f"_{tag}"):
            return f"{head[:-4]}__{rest}__{tag}"
    return stem


LABEL_PX, LABEL_MIN_W = 22, 320
TIER_PX = 16            # the production-tier line of a label strip: smaller than the label, in the tier's colour
TIER_KEY = "board_tier"  # a sheet PNG's text chunk: its tiles (or its strip) carry the tier, so it is stamped once


def _wrap(text: str, font, room: float) -> list[str]:
    """`text` in lines no wider than `room` (a long name breaks inside itself)."""
    from PIL import Image, ImageDraw
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines: list[str] = []
    cur = ""
    for word in str(text).split(" "):
        while probe.textlength(word, font=font) > room:   # a long name breaks inside itself
            k = max(1, int(len(word) * room / probe.textlength(word, font=font)) - 1)
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:k])
            word = word[k:]
        trial = f"{cur} {word}".strip()
        if cur and probe.textlength(trial, font=font) > room:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    lines.append(cur)
    return lines


def label(im, text: str, tier: tuple[str, str] | None = None):
    """The tile under its own label strip (black, white type, wrapped to the tile's width): a label never covers the
    image (F22: the @246 masthead tiles hid the mark under the bar). A tile narrower than LABEL_MIN_W sits on a
    board-grey field that wide, so its label stays readable. `tier` (cycle 5, review item 6: the tier was only in
    board.json): (tier, its words) drawn as the strip's first line, small, in the tier's colour; it is part of the
    strip, never of the image under it."""
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.load_default(size=LABEL_PX)
    if im.width < LABEL_MIN_W:
        field = Image.new("RGB", (LABEL_MIN_W, im.height), (40, 40, 40))
        field.paste(im, (0, 0))
        im = field
    room = im.width - 12
    lines = _wrap(text, f, room)
    step = LABEL_PX + 4
    tier_lines: list[str] = []
    tier_step = TIER_PX + 4
    if tier:
        ft = ImageFont.load_default(size=TIER_PX)
        tier_lines = _wrap(tier[1], ft, room)
    top = tier_step * len(tier_lines)
    h = 8 + top + step * len(lines)
    out = Image.new("RGB", (im.width, h + im.height), (0, 0, 0))
    d = ImageDraw.Draw(out)
    if tier:
        import board_evidence as be
        for i, ln in enumerate(tier_lines):
            d.text((6, 4 + tier_step * i), ln, fill=be.TIER_COLOURS.get(tier[0], (255, 255, 255)), font=ft)
    for i, ln in enumerate(lines):
        d.text((6, 4 + top + step * i), ln, fill=(255, 255, 255), font=f)
    out.paste(im, (0, h))
    return out


def _tier_info(tiers: str):
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text(TIER_KEY, tiers)
    return info


def tier_strip(im, stem: str):
    """A sheet saved whole (a table, a curve, a specimen: no tiles) under a one-line strip naming its tier
    (board_evidence.tile_tier of the sheet's own name): (the image with the strip above it, the tier). The sheet's
    own pixels are unchanged below the strip."""
    import board_evidence as be
    from PIL import Image
    t, words = be.tile_tier(stem, stem, WIDE_CAPTIONS)
    strip = label(Image.new("RGB", (max(im.width, LABEL_MIN_W), 1), (0, 0, 0)), stem, tier=(t, words))
    strip = strip.crop((0, 0, strip.width, strip.height - 1))
    out = Image.new("RGB", (max(im.width, strip.width), strip.height + im.height), (0, 0, 0))
    out.paste(strip, (0, 0))
    out.paste(im.convert("RGB"), (0, strip.height))
    return out, t


def tier_sheets(out: Path) -> dict[str, str]:
    """Cycle 5 (review item 6): every sheet of the board says its tier. sheet() draws it on each tile; this stamps
    the sheets saved whole (rows 1, 12 and 13's tables, curves and specimens, the burst and refusal tables) with
    tier_strip, and makes their half-size copy again from the stamped sheet. A sheet already carrying the tier (its
    PNG text chunk TIER_KEY) is left as it is, so a board re-run over the same folder stamps nothing twice. Only
    <out>/*.png: the surfaces and cases (the judged pixels) are in their own folders and are never opened here.
    Returns {sheet stem: tier} of the sheets stamped."""
    from PIL import Image
    done: dict[str, str] = {}
    for p in sorted(Path(out).glob("*.png")):
        with Image.open(p) as im:
            if im.info.get(TIER_KEY):
                continue
            stamped, t = tier_strip(im.convert("RGB"), p.stem)
        stamped.save(p, compress_level=1, pnginfo=_tier_info(t))
        half = p.with_name(p.stem + "_half.jpg")
        if half.is_file():
            stamped.resize((max(1, stamped.width // 2), max(1, stamped.height // 2)), Image.LANCZOS).save(half, quality=90)
        done[p.stem] = t
    return done


def sheet(tiles, out: Path, tile_h: int | None = 900, cols: int = 4) -> None:
    """Tiles (label, image) at a common height (`tile_h`), or at their own pixel size (`tile_h=None`: the feed
    renders, crops and tiles that must be seen as delivered), `cols` per row, each under its own label strip, saved
    full and half (Lanczos, q90)."""
    import board_evidence as be
    from PIL import Image
    if not tiles:
        return
    scaled = []
    tiers = []
    for name, im in tiles:
        im = im.convert("RGB")
        if tile_h is not None and im.height != tile_h:
            im = im.resize((max(1, round(im.width * tile_h / im.height)), tile_h), Image.LANCZOS)
        # cycle 5 (review item 6): each tile's production tier in its label strip (rows 1, 2, 12, 13 and the peer
        # feeds too: every sheet of tiles comes through here); the image under the strip is untouched
        tier = be.tile_tier(str(name), out.stem, WIDE_CAPTIONS)
        tiers.append(tier[0])
        tile = label(im, name, tier=tier)
        if reused_tile(str(name)):   # round 3: a copied, unproven surface says so on its own tile
            tile = not_evidence_strip(tile, "this tile's surface was copied from a previous board, not rendered")
        scaled.append(tile)
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
    # lossless either way; level 1 saves the big rows faster. The text chunk says its tiles carry their tier
    board.save(out.with_suffix(".png"), compress_level=1, pnginfo=_tier_info(",".join(sorted(set(tiers)))))
    board.resize((max(1, W // 2), max(1, H // 2)), Image.LANCZOS).save(out.with_name(out.stem + "_half.jpg"), quality=90)


# Round 3 (lander ruling 2026-11-26, queue/REVIEW2-board-c5.md): reuse is an iteration aid, never evidence. A board
# made with --reuse --unsafe-reuse-for-iteration names its copied surfaces here; their tiles, the surface files
# themselves and every sheet carry board_reuse.NOT_EVIDENCE, and the board is stamped not for the panel.
REUSED_STEMS: set[str] = set()
NOT_EVIDENCE_RED = (230, 40, 40)


def reused_tile(name: str) -> bool:
    """Whether a tile's label names a surface this board copied from a previous one (any ` | ` part of it)."""
    return bool(REUSED_STEMS) and any(part.strip() in REUSED_STEMS for part in name.split(" | "))


def not_evidence_strip(im, why: str):
    """`im` under a red strip that reads REUSED, NOT EVIDENCE and `why`; the image below the strip is unchanged."""
    import board_reuse
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.load_default(size=LABEL_PX)
    w = max(im.width, LABEL_MIN_W)
    lines = _wrap(f"{board_reuse.NOT_EVIDENCE}: {why}", f, w - 12)
    h = 8 + (LABEL_PX + 4) * len(lines)
    out = Image.new("RGB", (w, h + im.height), NOT_EVIDENCE_RED)
    d = ImageDraw.Draw(out)
    for i, ln in enumerate(lines):
        d.text((6, 4 + (LABEL_PX + 4) * i), ln, fill=(255, 255, 255), font=f)
    out.paste(im.convert("RGB"), (0, h))
    return out


def mark_not_evidence(out: Path) -> dict[str, list[str]]:
    """Every sheet saved in <out> (and its half-size copy) and every copied surface file under a NOT_EVIDENCE strip,
    once (the PNG text chunk board_reuse.NOT_EVIDENCE_KEY; the tier chunk is kept). Run after every row is drawn,
    so the rows were drawn from the copied pixels as they were. Returns what was marked."""
    import board_reuse
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    done: dict[str, list[str]] = {"sheets": [], "surfaces": []}
    targets = [(q, "sheets", "an incremental board (--unsafe-reuse-for-iteration): not for a panel or a score")
               for q in sorted(Path(out).glob("*.png"))]
    targets += [(Path(out) / "surfaces" / f"{n}.png", "surfaces", "copied from a previous board, not rendered")
                for n in sorted(REUSED_STEMS)]
    for q, kind, why in targets:
        if not q.is_file():
            continue
        with Image.open(q) as im:
            if im.info.get(board_reuse.NOT_EVIDENCE_KEY):
                continue
            texts = {k: v for k, v in im.info.items() if isinstance(v, str)}
            marked = not_evidence_strip(im.convert("RGB"), why)
        info = PngInfo()
        for k, v in texts.items():
            info.add_text(k, v)
        info.add_text(board_reuse.NOT_EVIDENCE_KEY, board_reuse.NOT_EVIDENCE)
        marked.save(q, compress_level=1, pnginfo=info)
        half = q.with_name(q.stem + "_half.jpg")
        if half.is_file():
            marked.resize((max(1, marked.width // 2), max(1, marked.height // 2)), Image.LANCZOS).save(half, quality=90)
        done[kind].append(q.name)
    return done


def _offline(*_a, **_k):
    raise RuntimeError("board.py is offline: no board path reaches the network (the paid matcher included)")


def block_network() -> None:
    """No socket connects from this process (the board process and every compose worker, where the Aswan deck and the
    board's own social frames are drawn), as render_surfaces.py blocks its own: board_decks.no_vision is then not the
    only thing between a board path and a paid or networked call."""
    import socket
    socket.create_connection = _offline
    socket.socket.connect = _offline
    socket.socket.connect_ex = _offline


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


def fit_sample_lines(lines, font, track: float, room: float) -> list[str]:
    """Freeze cycle 5 (ledger TEXT-06): a specimen sample's lines as drawn. Lines that fit are drawn as they are.
    Where one does not, the sample is ONE text re-broken by the system's breaker (src/line_policy.wrap) onto the same
    number of lines where it fits them; else only the LAST drawn line is cut, with a terminal "...". (Each line was cut on its own, so
    the tall sheet read "... in Mauritania that... / long fascinated observers": the word "has" was gone while the
    sentence carried on. A fitter never drops a word inside a sentence.)"""
    from src import line_policy

    def width(s):
        return font.getlength(s) + track * max(0, len(s) - 1)
    lines = list(lines)
    if all(width(t) <= room for t in lines):
        return lines
    text = " ".join(lines)
    got = line_policy.wrap(text, width, room, max_lines=len(lines))
    if got is not None:                      # the whole sample on its lines, broken by the one policy
        return got + [""] * 0
    words, out = text.split(), []
    for _ in range(len(lines) - 1):          # else: full lines, and the last one cut at its end
        k = 1
        while k < len(words) and width(" ".join(words[:k + 1])) <= room:
            k += 1
        out.append(" ".join(words[:k]))
        words = words[k:]
    out.append(fit_sample(" ".join(words), font, track, room))
    assert not line_policy.ellipsis_drop(" ".join(out), text), out
    return out


SPECIMEN_FACE_WEIGHTS = {"display": None, "text": 600, "data": 600, "masthead": 700}   # row 1's weight per face


def specimen_weight(role: str):
    """Freeze cycle 5 (ledger BOARD-05, one source): the weight a role draws at, from the token the renderers read
    (kicker.weight, credit.weight, badge.weight, masthead.weight, place.weight for labels), else its face's own. The
    specimen drew the credit at the data face's 600 while credit.weight says 500."""
    from src import brand_system
    token = {"kicker": "kicker.weight", "credit": "credit.weight", "badge": "badge.weight",
             "masthead": "masthead.weight"}.get(role)
    if token is not None and sys_value(token) is not None:
        return int(sys_value(token))
    return SPECIMEN_FACE_WEIGHTS[brand_system.FACE[role]]


def specimen_weights() -> dict[str, object]:
    """The weight row 1's specimen draws per role (the kicker at 800, else its face's), for row 13's weight table."""
    from src import brand_system
    return {role: specimen_weight(role) or "regular"
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
                weight = specimen_weight(role)
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
                lines = fit_sample_lines(lines, font, track, w - 48)
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
TREE: list[Path] = []        # cycle 5: the tree under review (main sets it), for the draws a worker runs


def prefetched(key):
    """A prefetched draw's value; its refusals join board_evidence.REFUSED here, where the inline draw records them."""
    import board_evidence as be
    return be.replay(PREFETCH.pop(key))


def _worker_init(tree: str, dev: str, here: str, brand_name: str) -> None:
    """A compose worker set up as main() sets up the board process (environment, paths, the exact memo, the brand)."""
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=dev)
    block_network()   # round 3: a compose worker is offline, as render_surfaces.py is
    os.chdir(tree)
    for p in (here, str(Path(tree) / FIX), tree):
        if p in sys.path:
            sys.path.remove(p)
    sys.path[0:0] = [here, str(Path(tree) / FIX), tree]
    import board_speed
    board_speed.install()
    import board_inputs   # cycle 3: this worker's reads join the board's input record (board_evidence.captured)
    board_inputs.install([dev, board_inputs.FRAME_CACHE, *board_inputs.tree_roots(tree)],
                         skip=[tempfile.gettempdir() + os.sep + "board-"])
    configure_palette(Path(tree), brand_name)


# The board's palettes (render_surfaces.py --brand): the default; "second", QF-35a's test brand (every palette colour
# with red and blue swapped, Montserrat in the display role, another masthead and end-card lines: the configure-refused
# evidence brand, 2026-11-19); "second-colours" (cycle 4, ruling 2026-11-19: BC4 re-theming is judged on it), the same
# test brand's colour set, masthead and lines with today's faces, so a fit refusal on it is a colour finding, never the
# display face's. The value is the display face second_brand takes (None: today's).
PALETTES = {"default": None, "second": "Montserrat", "second-colours": None}


def evidence_brand(tree: Path, brand_name: str):
    """The BrandConfig a palette other than the default draws in (render_surfaces.second_brand, the one definition
    render_surfaces.py --brand configures), or None for the default."""
    if brand_name == "default":
        return None
    qf35a = str(Path(tree) / "docs/reviews/quality-fixes-2026-11/QF-35a")
    if qf35a not in sys.path:
        sys.path.insert(0, qf35a)
    import render_surfaces

    from src import brand
    return render_surfaces.second_brand(brand, PALETTES[brand_name])


def configure_palette(tree: Path, brand_name: str) -> None:
    """The board process (or a compose worker) draws rows 1, 2, 12 and 13 in `brand_name`'s palette, as
    render_surfaces.py --brand draws the surfaces (configured unvalidated: evidence, the corpus's verdict recorded)."""
    cfg = evidence_brand(tree, brand_name)
    if cfg is not None:
        from src import brand
        brand.configure(cfg, validate=False)


def palette_record(brand_name: str, tree: Path) -> dict:
    """board.json "palette" (cycle 4): every colour role's default value and this palette's, the roles it leaves
    unmoved, and the faces and brand lines it changes, so a reviewer reads what the re-theme covers."""
    from src import brand
    base = brand.DEFAULT_BRAND
    cfg = evidence_brand(tree, brand_name) or base
    roles = {k: v for k, v in base.palette.items() if not isinstance(v, str)}
    moved = {k: (list(v), list(cfg.palette[k])) for k, v in roles.items() if tuple(cfg.palette[k]) != tuple(v)}
    return {"name": brand_name, "colour_roles": len(roles), "moved": len(moved),
            "unmoved": sorted(k for k in roles if k not in moved),
            "aliases": {k: v for k, v in base.palette.items() if isinstance(v, str)},
            "fonts_changed": {k: [base.fonts.get(k), v] for k, v in cfg.fonts.items() if base.fonts.get(k) != v},
            "text_changed": {k: [base.text.get(k), v] for k, v in cfg.text.items() if base.text.get(k) != v}}


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
    if rows & {11, 12}:   # cycle 4: the callout's held frame and its clip are one draw
        PREFETCH["callout held"] = pool.submit(be.captured, bx.callout_held, work, clips)
        done.append("row 11 callout held frame and row 12 callout clip")
    if 7 in rows:   # cycle 5: each caption fixture's whole run (row 7)
        for topic, tag in bx.caption_run_cases():
            PREFETCH[("caption run", topic, tag)] = pool.submit(be.captured, bx.caption_run, str(TREE[0]), work,
                                                                clips, topic, tag)
            done.append(f"caption run {topic} {tag}")
    if 12 in rows:   # cycle 4: the then-and-now clips (row 12)
        for topic, tag, _size, _case in bm.thennow_cases():
            bm.MOTION_PREFETCH[("thennow", topic, tag)] = pool.submit(be.captured, bm.draw_thennow, clips, work,
                                                                      topic, tag)
            done.append(f"then-and-now clip {topic} {tag}")
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
    # the hook: hold and fade, on every ground it draws on (cycle 5: the first keeps the unsuffixed names)
    hook = bm.hook_sequence(clips)
    for n_hook, (ground, one) in enumerate((hook.get("all") or {}).items()):
        hs = one["seq"]
        sname = "row12_motion_hook" + ("" if n_hook == 0 else f"__{ground}")
        cls, why = be.pairing(f"card__hook__{ground}__916")
        tag = f"{cls}: {why}" if cls != be.REAL else "own topic"
        clip_rec(hs, "12", f"hook card on the {ground} ground, claim {one['claim']} ({tag})")
        tiles = [(f"{n} | {tag}" if k == 0 else n, im)
                 for k, (n, im) in enumerate(x for x in bm.strip(hs, scale_h=480, label=f"hook ({ground}) ")
                                             if frame_no(x[0]) % 2 == 0 or 30 <= frame_no(x[0]) <= 42)]
        sheet(tiles, out / sname, tile_h=576, cols=8)
        t = [i / bm.FPS for i in range(hs.n)]
        bm.plot({"type alpha (_hook_alpha)": (t, [dc._hook_alpha(x) for x in t]),
                 "plate + wash alpha": (t, [dc._hook_alpha(x, dc.HOOK_GROUND_HOLD, dc.HOOK_GROUND_FADE) for x in t]),
                 "frame mean luma / 255": (t, [v / 255 for v in hs.luma])},
                f"hook card ({ground} ground, {tag}): the type's hold and fade, the ground's after it",
                "alpha (0-1)", (0, 1), marks={"HOOK_HOLD": dc.HOOK_HOLD, "HOOK_GROUND_HOLD": dc.HOOK_GROUND_HOLD,
                                               "CAPTION_CLEAR": dc.CAPTION_CLEAR}).save(out / f"{sname}_curves.png")
        made += [sname, f"{sname}_curves"]
    for g, why in hook.get("refused", {}).items():
        manifest["labels"][f"card__hook__{g}__916 (row 12)"] = f"REFUSED: {why}"
    made += thennow_motion(out, clips, work, clip_rec)   # cycle 4 (panel 5 MO): then-and-now and callout clips
    made += callout_motion(out, clips, work, clip_rec)
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


def callout_items(work: Path, clips: Path) -> list[dict]:
    """The callout's held frame (row 11) and its clip (row 12): one draw (board_extras.callout_held), by a worker
    when prefetched, kept for the other row."""
    import board_extras as bx
    if "callout held" in PREFETCH:
        bx._cache["callout"] = prefetched("callout held")
    return bx.callout_held(work, clips)


def motion_case(out: Path, sname: str, seq, label: str, rows: list[tuple], curves, scale_h: int) -> list[str]:
    """One motion case's evidence (cycle 4): the strip of its kept frames from the decoded clip, its curves and its
    timing table (png and json)."""
    import board_motion as bm
    tiles = [(f"{n} | {label}" if k == 0 else n, im)
             for k, (n, im) in enumerate(bm.strip(seq, scale_h=scale_h, label=f"{sname.split('__', 1)[-1]} "))]
    sheet(tiles, out / sname, tile_h=scale_h, cols=8)
    curves.save(out / f"{sname}_curves.png")
    draw_table(rows, (420, 200, 140, 900)).save(out / f"{sname}_timing.png")
    (out / f"{sname}_timing.json").write_text(json.dumps([dict(zip(rows[0], r)) for r in rows[1:]], indent=1,
                                                         default=str), encoding="utf-8", newline="\n")
    return [sname, f"{sname}_curves", f"{sname}_timing"]


def thennow_motion(out: Path, clips: Path, work: Path, clip_rec) -> list[str]:
    """Row 12, cycle 4 (panel 5 MO: then-and-now judged from stills only): every then-and-now case of the catalogue
    in both formats, drawn and encoded once by its renderer, decoded once: strip, curves and timing table each."""
    import board_evidence as be
    import board_motion as bm
    made = []
    for name, seq in bm.thennow_sequences(clips, work).items():
        cls, why = be.pairing(name)
        tag = f" ({cls}: {why})" if cls != be.REAL else " (the run's own pair)"
        clip_rec(seq, "12", f"then-and-now {seq.topic} {seq.tag}{tag}")
        made += motion_case(out, f"row12_motion_{name}", seq, f"then-and-now {seq.topic} {seq.tag}{tag}, decoded",
                            bm.thennow_timing(seq), bm.thennow_curves(seq), 576 if seq.tag == "916" else 324)
    return made


def callout_motion(out: Path, clips: Path, work: Path, clip_rec) -> list[str]:
    """Row 12, cycle 4 (panel 5 MO: the callout judged from its held still only): the callout clip as its renderer
    encodes it, decoded once: strip, curves and timing table from the renderer's own per-frame record."""
    import board_evidence as be
    import board_motion as bm
    callout_items(work, clips)
    seq = bm.callout_sequence(clips, work)
    if seq is None:
        return []
    cls, why = be.pairing(seq.name)
    label = f"photo callout 9:16 ({cls}: {why}), decoded"
    clip_rec(seq, "12", label)
    return motion_case(out, "row12_motion_callouts__reference_pair__916", seq, label, bm.callout_timing(seq),
                       bm.callout_curves(seq), 576)


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


def row13_table(out: Path, work: Path, board_mastheads: dict | None = None) -> list[str]:
    """The element-by-element tables across surfaces, generated (cycle 3: never written by hand, so never stale):
    what this board's renderers recorded as drawn (board_table.drawn), what each surface's code reaches
    (board_table.code_rows), the weights the system holds and who reads them (board_table.weight_rows), and the
    per-canvas token table (element_rows); the motion table adds each end sequence's delivered length."""
    from PIL import Image, ImageDraw, ImageFont

    import board_motion as bm
    import board_table as bt
    ends = bm.all_end()
    sj = out / "surfaces.json"
    surfaces = json.loads(sj.read_text(encoding="utf-8")) if sj.is_file() else None
    if board_mastheads:   # cycle 5: the board-drawn social frames (the Aswan deck, the near-white slides) too
        surfaces = dict(surfaces or {})
        surfaces["masthead_records"] = {**(surfaces.get("masthead_records") or {}), **board_mastheads}
    parts = [
        ("Drawn on this board: each renderer's own masthead record per surface across its grounds (row 2 records, "
         "and the end sequences' stats of rows 9 / 12)", draw_table(bt.drawn(out, ends), (420, 190, 190, 230, 240, 230, 520))),
        ("Drawn on this board: the masthead of every social frame by kind, cover and interiors (each frame's own "
         "layout record from render_surfaces.py; row13_fb_masthead shows the regions)",
         draw_table(bt.social_masthead_rows(surfaces), (380, 150, 170, 190, 150, 560, 170, 520, 150, 150, 150))),
        ("From the code: each surface's entry point and what it reaches (an AST call graph over src/*.py; colours and "
         "tokens named in the surface's own module)", draw_table(bt.code_rows(), (560, 380, 330, 420, 760))),
        ("Weights: every <role>.weight value, the src modules that read it, and what row 1's specimen draws",
         draw_table(bt.weight_rows(specimen_weights()), (300, 120, 220, 560, 300))),
        ("Per element and canvas, from src/brand_system.py as this tree holds it (TYPE / TYPE_FALLBACK / TYPE_FLOOR / "
         "FACE / SPACING, gates, runtime tokens and SPEC):", draw_table(element_rows(), (250, 520, 520, 520, 590))),
    ]
    # SYS-E-2 (lane E cycle 3, panel 3 BC 15): the gold rule under the wordmark, read from end_card.layout (the
    # renderer's own layout on each canvas, with the line it carries there), never a separate call with another room
    from src import brand, end_card
    wide = end_card.layout(brand.HANDOFF_LINE, 1920, 1080)
    tall = end_card.layout(brand.FOLLOW_LINE, 1080, 1920)
    parts.append(("The gold rule under the wordmark, from end_card.layout on each canvas (the renderer's own layout)",
                  draw_table([("canvas", "rule", "draws out"),
                              ("end screen 16:9 (hand-off line)", f"GOLD, {wide['rule'][1] - wide['rule'][0]} px",
                               f"{end_card.RULE_AT:g}-{end_card.RULE_AT + end_card.RULE_IN:g} s"),
                              ("end card 9:16 (follow line)", f"GOLD, {tall['rule'][1] - tall['rule'][0]} px",
                               "same timing")], (420, 300, 300))))
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
    made = ["row13_elements"] + fb_masthead_sheet(out, surfaces)
    # round 8 (MO-1): the motion tokens (durations, eases, overshoot) as this tree holds and draws them; cycle 3
    # (MO r1): each end sequence's delivered length beside the constant that names it
    asses = {f"{c['topic']} {c['format']}": c["ass"] for c in caption_cases(work)}
    mrows = bm.motion_rows(asses) + bt.delivered_motion(ends)
    draw_table(mrows, (420, 560, 300, 760)).save(out / "row13_motion_tokens.png")
    (out / "row13_motion_tokens.json").write_text(json.dumps([dict(zip(mrows[0], r)) for r in mrows[1:]], indent=1,
                                                             default=str), encoding="utf-8", newline="\n")
    return made + ["row13_motion_tokens"]


MASTHEAD_REGION = (0.0, 0.0, 0.62, 0.2)   # the top-left share of a 4:5 frame the masthead sits in (HEADER_Y / FB)


def fb_masthead_sheet(out: Path, surfaces: dict | None) -> list[str]:
    """Cycle 4 (panel 5 BC, carry-in 3): per deck, the Instagram cover's masthead region beside the Facebook cover's
    (its own variant: FB_HEADER_Y, the matched reach) and every Facebook interior's (which draws none by rule), each
    labelled with its frame's own masthead record; the whole Facebook cover at 1x beside them. Read from this board's
    surfaces, nothing drawn again. [] when the board has no Facebook frames."""
    import board_evidence as be
    import board_table as bt
    from PIL import Image
    recs = (surfaces or {}).get("masthead_records") or {}
    pngs = {p.stem: p for p in sorted((out / "surfaces").glob("social__*.png"))}
    decks: dict[str, dict] = {}
    for stem in pngs:
        part = bt.social_part(stem)
        if part and part[1] in ("carousel", "fb"):
            decks.setdefault(part[0], {}).setdefault(part[1], []).append((part[2], stem))
    tiles = []

    def state(stem: str) -> str:
        r = recs.get(stem)
        if r is None:
            return "no masthead record"
        if r.get("shown") is False:
            return f"NOT DRAWN: {r.get('reason')}"
        return (f"{r.get('variant', 'bone')} {r.get('size')} px, {r.get('contrast')}:1 (worst letter)"
                + (f", reach matched {r['reach_matched']}" if r.get("reach_matched") else ""))
    for deck, kinds in sorted(decks.items()):
        fb = sorted(kinds.get("fb") or [])
        if not fb:
            continue
        order = [s for k, s in sorted(kinds.get("carousel") or []) if k == 1] + [s for _k, s in fb]
        for stem in order:
            im = Image.open(pngs[stem]).convert("RGB")
            w, h = im.size
            box = tuple(round(v * (w if i % 2 == 0 else h)) for i, v in enumerate(MASTHEAD_REGION))
            what = "Instagram cover" if "__carousel_" in stem else (
                "Facebook cover" if stem.endswith("_01") else "Facebook interior")
            tiles.append((be.label(stem, f"{what} masthead region | {state(stem)}"), im.crop(box)))
        tiles.append((be.label(fb[0][1], "Facebook cover, whole, 1x"), Image.open(pngs[fb[0][1]]).convert("RGB")))
    if not tiles:
        return []
    sheet(tiles, out / "row13_fb_masthead", tile_h=None, cols=5)
    return ["row13_fb_masthead"]


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


def row7_runs(out: Path, tree: Path, work: Path, clips: Path, manifest: dict) -> dict[str, list[str]]:
    """Row 7, cycle 5: per caption fixture, the whole run: its production clip, the caption band at the end of every
    burst at 1:1 (row07_captions_run__<topic>__<tag>.png) and the burst table (…_bursts.png / .json). A worker draws
    each under --workers (prefetch_compose)."""
    import board_extras as bx
    import board_motion as bm
    made: dict[str, list[str]] = {}
    for topic, tag in bx.caption_run_cases():
        key = ("caption run", topic, tag)
        try:
            res = prefetched(key) if key in PREFETCH else bx.caption_run(tree, work, clips, topic, tag)
        except Exception:  # noqa: BLE001  one fixture's failure is recorded, the others are drawn
            manifest["errors"][f"caption run {topic} {tag}"] = traceback.format_exc()[-1500:]
            continue
        if not res.get("clip"):
            continue
        sname = f"row07_{res['name']}"
        tiles = res["tiles"]
        if tiles:
            head = (f"{topic} {tag}: the caption band {res['box']} at 1:1 on the frame {bx.CAPTION_RUN_LEAD}s before "
                    f"each burst leaves (its reveal complete), production encode, on {res['ground']}")
            tiles = [(f"{tiles[0][0]} | {head}", tiles[0][1])] + tiles[1:]
        sheet(tiles, out / sname, tile_h=None, cols=4)
        draw_table(res["rows"], (90, 130, 140, 90, 190, 200, 1100)).save(out / f"{sname}_bursts.png")
        (out / f"{sname}_bursts.json").write_text(json.dumps(
            [dict(zip(res["rows"][0], r)) for r in res["rows"][1:]], indent=1, default=str), encoding="utf-8",
            newline="\n")
        manifest["clips"][res["clip"]["path"].name] = {
            "row": "7", "what": f"captions, whole run: {topic} {tag}, {len(res['rows']) - 1} bursts, "
                                f"{res['seconds']} s on {res['ground']}",
            "frames": res["frames"], "seconds": round(res["frames"] / bm.FPS, 3), "sha256": res["clip"]["sha256"],
            "encoder": res["clip"]["encoder"]}
        made[sname] = [n.split(" | ")[0] for n, _ in tiles]
    return made


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
                f"accent GOLD {hexc(brand.GOLD)}, rest BONE {hexc(brand.BONE)}; {bs.ACCENT_RULE}; line ends never "
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


def fb_post_order(deck: str, names: list[str], sj_rec: dict, dev: Path) -> tuple[list[str], str, str | None]:
    """The Facebook slides of `deck` the multi-photo post carries, in its order: (surface names, over what the pick
    was made, the error when facebook_pick could not run). A deck drawn by production's entry carries the publisher's
    own pick over the copy as drawn (photo_choice "facebook_post", board_decks.production_deck); a replayed deck is
    picked over the run's recorded Facebook copy with today's archetypes."""
    import board_evidence as be
    choice = sj_rec.get("photo_choice") or {}
    places = {n: (choice.get(n) or {}).get("facebook_post") for n in names if "facebook_post" in (choice.get(n) or {})}
    if places and all(v != "unknown" for v in places.values()):
        return sorted((n for n, v in places.items() if v), key=lambda n: places[n]), "the Facebook copy as drawn", None
    current = {f"carousel_slide_{k.rsplit('_', 1)[-1]}.jpg": v["current"] for k, v in (sj_rec.get("rerouted") or {}).items()
               if k.startswith(f"{deck}__fb_carousel_slide_")}
    try:
        picked = be.fb_post_slides(dev / "output" / f"run_{deck[len('social__'):]}", current)
    except Exception as e:  # noqa: BLE001  recorded; the deck's own order stands in, labelled
        return [], "the run's recorded Facebook copy with today's archetypes", f"{type(e).__name__}: {e}"[:1500]
    return ([f"{deck}__fb_carousel_slide_{Path(x).stem.rsplit('_', 1)[-1]}" for x in picked],
            "the run's recorded Facebook copy with today's archetypes", None)


def fb_unposted(names: list[str], sj_rec: dict, dev: Path) -> dict[str, list[str]]:
    """Round 3 (lander ruling 2026-11-26, after REVIEW2-board-c5.md (d)): per deck, the Facebook slides the
    multi-photo post never carries (fb_post_order), which the usage inventory counts as never seen by viewers: tier 4
    (board_evidence.FB_UNPOSTED). A deck whose pick could not be made keeps its slides at tier 1, said in board.json."""
    decks: dict[str, list[str]] = {}
    for n in sorted(x for x in names if x.startswith("social__") and "__fb_carousel_slide_" in x):
        decks.setdefault(n.split("__fb_carousel_slide_")[0], []).append(n)
    out: dict[str, list[str]] = {}
    for deck, slides in decks.items():
        order, _over, err = fb_post_order(deck, slides, sj_rec, dev)
        if order and not err:
            out[deck] = [n for n in slides if n not in order]
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
    # cycle 3 (lane D): the post holds publisher's set and order (multi_format.facebook_pick over the run's Facebook
    # copy, with today's archetypes), never simply the deck's first five
    decks: dict[str, list] = {}
    for n in sorted(x for x in surf if x.startswith("social__") and "__fb_carousel_slide_" in x):
        decks.setdefault(n.split("__fb_carousel_slide_")[0], []).append(n)
    sj_ = out / "surfaces.json"
    sj_rec = json.loads(sj_.read_text(encoding="utf-8")) if sj_.is_file() else {}
    collages, posted = [], {}
    for deck, names in decks.items():
        tag = deck[len("social__"):]
        order, over, err = fb_post_order(deck, names, sj_rec, dev)
        if err:
            manifest["errors"][f"peer feeds: facebook_pick {tag}"] = err
        order = [n for n in order if n in surf]
        how = (f"publisher's set: facebook_pick over {over} posts {len(order)} of {len(names)} Facebook slides "
               f"({', '.join(n.rsplit('_', 1)[-1] for n in order)})" if order else
               f"NOT publisher's set (facebook_pick unavailable): the first {min(len(names), 5)} of {len(names)}")
        order = order or names[:5]
        collages.append((f"{deck}__fb_collage", be.fb_collage([Image.open(surf[n]).convert("RGB") for n in order])))
        posted[f"{deck}__fb_collage"] = how
    manifest.setdefault("fb_posts", {}).update(posted)
    first_label.update({n: f"FB MULTI-PHOTO COLLAGE as the feed shows it, {posted[n]} | {be.label(n)}"
                        for n, _ in collages})
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
                   "retired", "rerouted", "bridge_ladder", "wide_grounds",   # cycle 3: retired frames not drawn, slides re-routed
                   # cycle 4: the omission ledger per job (SYS batch 3, lane A) and per surface (render_surfaces.py
                   # attributes each row to the surface it was drawn into), and each frame's masthead record
                   "omissions", "surface_omissions", "masthead_records",
                   # cycle 5: each slide's photo choice (SYS-photo-c5-1), each Story frame's pointer (SYS-plates-c5-1)
                   "photo_choice", "pointer_records")
SHARD_SAME_KEYS = ("src_sha256", "brand", "graphic_runs", "brand_validation")   # equal in every shard
SHARD_UNION_KEYS = ("input_files",)   # cycle 3: {path: sha256} read by a shard (board_inputs.py), merged by union
SHARD_SKIP_KEYS = ("count",)   # recomputed after the merge
SHARD_COPY_SET_KEYS = ("copy_set",)   # --copy-set's record: its file and hash equal in every shard, "swapped" a union


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
    own): a key outside the known ones, a differing shared value or a surface in two shards is an error. Every key
    render_surfaces.py can write is in exactly one of SHARD_ONLY_KEYS / SHARD_UNION_KEYS / SHARD_SAME_KEYS /
    SHARD_SKIP_KEYS (test_freeze_board_c4 reads them from its source, so a new record key cannot crash --workers)."""
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
            elif k in SHARD_COPY_SET_KEYS:
                have = merged.get(k)
                rest = {x: y for x, y in v.items() if x != "swapped"}
                if have is not None and {x: y for x, y in have.items() if x != "swapped"} != rest:
                    raise RuntimeError(f"surfaces shards: {k} differs between shards")
                merged[k] = {**rest, "swapped": sorted(set((have or {}).get("swapped") or []) | set(v.get("swapped") or []))}
            elif k not in SHARD_SKIP_KEYS:
                raise RuntimeError(f"surfaces shards: unknown key {k!r} (board.py merge_shards needs it)")
    merged["count"] = len(merged.get("surfaces", {}))
    return merged


def surfaces_cmd(here: Path, tree: Path, dev: Path, per_kind: int, brand_name: str) -> tuple[list[str], list[str]]:
    """(run_surfaces.py's command up to render_surfaces.py's own arguments, the arguments every call shares)."""
    script = tree / "docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py"
    base = [sys.executable, str(here / "run_surfaces.py"), str(script), str(tree), str(dev)]
    return base, ["--per-kind", str(per_kind)] + (["--brand", brand_name] if brand_name != "default" else [])


def offered_jobs(here: Path, tree: Path, dev: Path, per_kind: int, brand_name: str, subset: Path | None) -> list[str]:
    """The jobs render_surfaces.py would render now (its --list-jobs, after --per-kind and --surface-jobs), in order."""
    base, common = surfaces_cmd(here, tree, dev, per_kind, brand_name)
    tmp = Path(tempfile.mkdtemp(prefix="board-jobs-"))
    try:
        subprocess.run(base + [str(tmp / "listing.json")] + common + (["--jobs", str(subset)] if subset else [])
                       + ["--list-jobs", str(tmp / "jobs.txt")], check=True, stdout=subprocess.DEVNULL)
        return (tmp / "jobs.txt").read_text(encoding="utf-8").splitlines()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_jobs(base: list[str], common: list[str], keep: list[str], out: Path, env: dict, names: list[str],
             prior: dict[str, float], workers: int, tree: Path, before: dict[str, str] | None, tmp: Path,
             clean: bool = True) -> dict:
    """One render process per job, `workers` at a time, longest first (cycle 5, fix 1): what a job reads, imports
    or leaves in a module is then its own, so its record (board_deps.py) is whole and does not depend on which jobs
    shared its process. Merged into <out>/surfaces.json and <out>/DEPS.json as the shards were."""
    import threading

    import board_deps
    est = {k: float(prior.get(k) or JOB_SECONDS.get(k.split(" ")[0], 60)) for k in names}
    queue = sorted(names, key=lambda k: (-est[k], names.index(k)))
    lock = threading.Lock()
    failed: dict[int, int] = {}
    wall: dict[str, float] = {}
    env = dict(env, BOARD_DEPS_SKIP=os.pathsep.join([str(out), str(tmp)]))

    def worker() -> None:
        while True:
            with lock:
                if not queue:
                    return
                name = queue.pop(0)
            k = names.index(name)
            (tmp / f"job{k}.jobs").write_text(f"{name}\n", encoding="utf-8", newline="\n")
            t0 = time.perf_counter()
            with open(tmp / f"job{k}.log", "w", encoding="utf-8") as log:
                rc = subprocess.run(base + [str(tmp / f"job{k}.json")] + keep + common
                                    + ["--jobs", str(tmp / f"job{k}.jobs")], stdout=log, stderr=subprocess.STDOUT,
                                    env=env).returncode
            wall[name] = round(time.perf_counter() - t0, 1)
            if rc:
                failed[k] = rc
                print(f"board: surfaces job {name!r} failed ({rc}):\n"
                      + (tmp / f"job{k}.log").read_text(encoding="utf-8", errors="replace")[-3000:], file=sys.stderr)
    print(f"board: surfaces: {len(names)} jobs, one process each, {workers} at a time, logs in {tmp}",
          file=sys.stderr, flush=True)
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, min(workers, len(names))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if failed:
        raise RuntimeError(f"surfaces jobs {sorted(names[k] for k in failed)[:6]} failed (logs in {tmp})")
    merged = merge_shards([json.loads((tmp / f"job{k}.json").read_text(encoding="utf-8")) for k in range(len(names))])
    missing = [n for n in names if n not in merged.get("seconds", {})]
    if missing:
        raise RuntimeError(f"surfaces jobs not run {missing[:5]} (logs in {tmp})")
    (out / "surfaces.json").write_text(json.dumps(merged, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    parts = [json.loads(p.read_text(encoding="utf-8")) for p in (tmp / f"job{k}.deps.json" for k in range(len(names)))
             if p.is_file()]
    record = board_deps.merge(parts)
    summary = board_deps.resolve(record, board_deps.Tree(tree, "HEAD"), before, remember=clean)
    (out / "DEPS.json").write_text(json.dumps(record, indent=0, sort_keys=True), encoding="utf-8", newline="\n")
    print(merged["count"], "surfaces,", len(merged.get("errors", {})), "errors,",
          round(sum(merged["seconds"].values())), "s of jobs in", len(names), "processes")
    shutil.rmtree(tmp, ignore_errors=True)
    return {"processes": len(names), "deps": True, "process_seconds": wall, "artefacts": summary,
            "startup_seconds": round(sum(wall.values()) - sum(merged["seconds"].values()), 1)}


def run_surfaces(here: Path, tree: Path, dev: Path, out: Path, per_kind: int, brand_name: str, workers: int,
                 timings: Path | None, subset: Path | None, deps: bool = False,
                 before: dict[str, str] | None = None, clean: bool = True) -> dict:
    """render_surfaces.py through run_surfaces.py into <out>/surfaces and <out>/surfaces.json. With `deps` (the
    default, cycle 5): one process per job (run_jobs), each recording everything its job can depend on
    (board_deps.py) into <out>/DEPS.json; a record only, the surfaces are the same bytes. Without: one process, or
    `workers` shards (--jobs) merged into the same surfaces.json, and no record (the board is no reuse base).
    Returns what ran (for board.json). `before`: the tree's untracked files when the board started; `clean`: the
    worktree is its HEAD (only then are observed builds remembered in the builders' store)."""
    script = tree / "docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py"
    base, common = surfaces_cmd(here, tree, dev, per_kind, brand_name)
    keep = ["--keep", str(out / "surfaces")]
    listable = "--list-jobs" in script.read_text(encoding="utf-8")
    if deps and not listable:
        print("board: this tree's render_surfaces.py has no --list-jobs: no per-job record", file=sys.stderr)
        deps = False
    env = dict(os.environ, BOARD_DEPS="1") if deps else dict(os.environ, BOARD_DEPS="0")
    (out / "DEPS.json").unlink(missing_ok=True)   # never a stale record beside surfaces it does not describe
    if workers > 1 and not listable:
        print("board: this tree's render_surfaces.py has no --list-jobs: surfaces in one process", file=sys.stderr)
        workers = 1
    if workers <= 1 and not deps:
        subprocess.run(base + [str(out / "surfaces.json")] + keep + common + (["--jobs", str(subset)] if subset else []),
                       check=True, env=env)
        return {"processes": 1, "deps": False}
    tmp = Path(tempfile.mkdtemp(prefix="board-shards-"))
    listing = tmp / "jobs.txt"
    subprocess.run(base + [str(tmp / "listing.json")] + common + (["--jobs", str(subset)] if subset else [])
                   + ["--list-jobs", str(listing)], check=True, stdout=subprocess.DEVNULL, env=env)
    names = listing.read_text(encoding="utf-8").splitlines()
    src = timings or (out / "surfaces.json")
    prior = {}
    if src.is_file():
        try:
            prior = json.loads(src.read_text(encoding="utf-8")).get("seconds") or {}
        except ValueError:
            prior = {}
    if deps:
        return run_jobs(base, common, keep, out, env, names, prior, workers, tree, before, tmp, clean)
    shards = [s for s in shard_jobs(names, prior, workers) if s]
    procs = []
    for k, jobs in enumerate(shards):
        (tmp / f"shard{k}.jobs").write_text("".join(f"{j}\n" for j in jobs), encoding="utf-8", newline="\n")
        log = open(tmp / f"shard{k}.log", "w", encoding="utf-8")
        cmd = base + [str(tmp / f"shard{k}.json")] + keep + common + ["--jobs", str(tmp / f"shard{k}.jobs")]
        procs.append((k, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env), log,
                      time.perf_counter()))
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
            "deps": deps,
            "shard_jobs_seconds": [round(sum(merged["seconds"].get(j) or 0 for j in s), 1) for s in shards],
            "shard_seconds": [took_s[k] for k in range(len(shards))]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tree", type=Path)
    ap.add_argument("dev", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--brand", choices=tuple(PALETTES), default="default")
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
    ap.add_argument("--reuse", type=Path, help="cycle 5, the incremental board: a previous board of this palette "
                    "(its board.json, surfaces.json, DEPS.json, MANIFEST.json). Only the render_surfaces.py jobs whose "
                    "inputs changed since it are rendered (board_reuse.py says what an input is); the rest are its "
                    "files, byte for byte; REUSE.json records which. Anything that forbids it renders every job")
    ap.add_argument("--unsafe-reuse-for-iteration", action="store_true", help="round 3 (queue/REVIEW2-board-c5.md): "
                    "--reuse is refused without it. With it the board is an iteration aid, never evidence: stamped "
                    "not for the panel (board.json not_for_panel, REUSE.json unsafe, NOT-EVIDENCE.txt), every copied "
                    "tile and surface file and every sheet marked REUSED, NOT EVIDENCE")
    ap.add_argument("--reuse-sample", type=int, default=2, help="with --reuse: this many of the reused jobs that "
                    "loaded a changed module, picked at random, are rendered anyway and must equal the previous "
                    "board's (a difference: exit 3); -1: all of them. The jobs the static net names are always "
                    "rendered (board_reuse.static_net)")
    ap.add_argument("--reuse-seed", type=int, help="with --reuse: the sample's seed (default: random, recorded)")
    ap.add_argument("--no-deps", action="store_true", help="do not record the code each job runs (DEPS.json): the "
                    "board cannot then be the --reuse base of a later one")
    ap.add_argument("--end-topics", default="", help="rows 9 / 12: only these board grounds' end sequences "
                    "(comma-separated; default every board ground with a catalogue end-card case)")
    ap.add_argument("--portability", type=Path, help="a lane's freeze candidate: a file of render_surfaces.py job "
                    "names (as --surface-jobs). Renders them in the default palette, the second palette, its colours with "
                    "today's faces (second-colours) and the "
                    "default palette on the non-history stress copy set (board_portability.py), side by side, into "
                    "<out>/portability; nothing else is drawn")
    a = ap.parse_args()
    import board_reuse
    if a.reuse and not a.unsafe_reuse_for_iteration:   # round 3: the fence (board_reuse.REFUSED)
        print(f"board: {board_reuse.REFUSED}", file=sys.stderr, flush=True)
        raise SystemExit(2)
    block_network()   # round 3: the board process is offline too (the Aswan deck is drawn here at --workers 1)
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
    board_inputs.install([dev, board_inputs.FRAME_CACHE, *board_inputs.tree_roots(tree)], skip=[out])
    stamp = board_inputs.tree_stamp(tree, here)
    stamp["path"] = str(tree)   # cycle 5: where the tree was (its recorded data reads are under it)
    if not a.no_deps:   # cycle 5, fix 1: the files no commit names that a render could read (an on-disk cache)
        import board_deps
        stamp["untracked"] = board_deps.untracked(tree)
    src_hash = hashlib.sha256()
    for f in sorted((tree / "src").glob("*.py")):
        src_hash.update(f.name.encode() + f.read_bytes().replace(bytes([13, 10]), bytes([10])))
    manifest = {"src_sha256": src_hash.hexdigest()[:16], "brand": a.brand, "rows": {}, "heroes": HEROES, "seconds": {},
                "tree": stamp}
    t_all = time.perf_counter()
    import board_motion as bm
    TREE[:] = [tree]
    global WIDE_CAPTIONS
    try:
        import fixtures as _fx
        WIDE_CAPTIONS = tuple(c["topic"] for c in _fx.load("captions") if c.get("format") == "16:9")
    except Exception:  # noqa: BLE001  the tiers then read every caption as 9:16; said in board.json
        manifest["tier_note"] = "caption formats unreadable: every caption case tiered as 9:16"
    END_TOPICS[:] = [t for t in bm.end_topics(HEROES)
                     if not a.end_topics or t in {x.strip() for x in a.end_topics.split(",")}]
    manifest["end_topics"] = list(END_TOPICS)

    def took(what: str, t0: float) -> None:
        manifest["seconds"][what] = round(time.perf_counter() - t0, 1)
        print(f"board: {what}: {manifest['seconds'][what]:.0f}s", file=sys.stderr, flush=True)
    t0 = time.perf_counter()   # cycle 5: board_reuse, the not-code inputs outside git (toolchain, tools/ data)
    reuse_env = manifest["reuse_env"] = board_reuse.environment(tree)
    took("reuse environment (toolchain, tools/ data hashed)", t0)
    if rows & set(ROWS) and not a.no_surfaces:
        t0 = time.perf_counter()
        subset = a.surface_jobs.resolve() if a.surface_jobs else None
        timings = a.timings.resolve() if a.timings else None
        plan = None
        if a.reuse:   # cycle 5: the incremental board (board_reuse.py): only the jobs whose inputs changed
            prev = a.reuse.resolve()
            # round 3: with the flag (checked above) the board is never evidence, whatever is reused
            manifest["not_for_panel"] = (f"{board_reuse.NOT_EVIDENCE}: made with --reuse {prev} "
                                         f"{board_reuse.UNSAFE_FLAG} (queue/REVIEW2-board-c5.md R1-R5)")
            board_reuse.stamp_not_evidence(out, [])
            try:
                offered = offered_jobs(here, tree, dev, a.per_kind, a.brand, subset)
                t_plan = time.perf_counter()
                took("reuse: the job list (render_surfaces.py --list-jobs)", t0)
                plan = board_reuse.plan(tree, prev, a.brand, stamp, reuse_env, offered, a.reuse_sample, a.reuse_seed)
                took("reuse: the plan (each job's key at both commits, its inputs on disk, the static net)", t_plan)
            except board_reuse.NoReuse as e:
                print(f"board: NO REUSE of {prev}: {e}: rendering every job", file=sys.stderr, flush=True)
                manifest["reuse"] = {"prev": str(prev), "no_reuse": str(e)}
                rec = board_reuse.none(out, str(e), a.brand, prev)
                rec["unsafe"] = manifest["not_for_panel"]
                (out / board_reuse.REUSE_NAME).write_text(json.dumps(rec, indent=1), encoding="utf-8", newline="\n")
        if plan is None:
            (out / board_reuse.REUSE_NAME).unlink(missing_ok=True) if not a.reuse else None
            manifest["workers"] = {"surfaces": run_surfaces(here, tree, dev, out, a.per_kind, a.brand, a.workers,
                                                            timings, subset, deps=not a.no_deps,
                                                            before=stamp.get("untracked"),
                                                            clean=stamp.get("dirty_vs_head") is False)}
        else:
            todo = board_reuse.to_render(plan)
            print(f"board: reuse of {plan['prev']['dir']} ({plan['prev']['head'][:10]}): {len(todo)} of "
                  f"{len(plan['jobs'])} jobs render ({len(plan['sample']['static_net'])} of them for the static "
                  f"net, {len(plan['sample']['random'])} the random sample)", file=sys.stderr, flush=True)
            for j in todo:
                r = plan["jobs"][j]
                print(f"board:   {r['status']:8} {j}: {r.get('why', 'sample')} "
                      f"{'; '.join(c.rsplit('/', 1)[-1] for c in (r.get('changed') or [])[:4])}", file=sys.stderr)
            fresh = fresh_deps = None
            if todo:
                jobs_file = Path(tempfile.mkdtemp(prefix="board-reuse-")) / "jobs.txt"
                jobs_file.write_text("".join(f"{j}\n" for j in todo), encoding="utf-8", newline="\n")
                manifest["workers"] = {"surfaces": run_surfaces(here, tree, dev, out, a.per_kind, a.brand,
                                                                min(a.workers, len(todo)), timings or
                                                                (a.reuse.resolve() / "surfaces.json"), jobs_file,
                                                                deps=True, before=stamp.get("untracked"),
                                                            clean=stamp.get("dirty_vs_head") is False)}
                fresh = json.loads((out / "surfaces.json").read_text(encoding="utf-8"))
                fresh_deps = json.loads((out / "DEPS.json").read_text(encoding="utf-8"))
                shutil.rmtree(jobs_file.parent, ignore_errors=True)
            plan["unsafe"] = manifest["not_for_panel"]   # REUSE.json says it too (finish writes it)
            merged = board_reuse.finish(plan, out, fresh, fresh_deps)   # exit 3 on a sample mismatch
            REUSED_STEMS.update(Path(f).stem for f, r in plan["files"].items() if r["status"] == "reused")
            board_reuse.stamp_not_evidence(out, sorted(REUSED_STEMS))
            merged["src_sha256"] = manifest["src_sha256"]
            (out / "surfaces.json").write_text(json.dumps(merged, indent=1, sort_keys=True), encoding="utf-8",
                                               newline="\n")
            manifest["reuse"] = {"prev": plan["prev"], "counts": plan["counts"], "sample": plan["sample"],
                                 "record": board_reuse.REUSE_NAME}
            print(f"board: reuse: {plan['counts']}", file=sys.stderr, flush=True)
        took("surfaces (render_surfaces.py)", t0)
    from src import brand
    manifest["palette"] = palette_record(a.brand, tree)   # cycle 4: what this palette moves (BC4)
    configure_palette(tree, a.brand)   # rows 1, 2, 12, 13 in the palette too (round 8: evidence, unvalidated)
    import board_evidence as be
    if a.brand != "default":   # the configure-time validation's own list, in this palette's refusal table
        try:
            from src.brand_system import validate_display
            for why in validate_display(brand.active()) or []:
                be.REFUSED.append(("brand (configure-time fit corpus)", "brand refused at configure", str(why)[:400]))
        except ImportError:
            pass
        try:   # freeze cycle 6 (MASTHEAD M3): and the palette's masthead pair on the stored grounds, its value
            from src.masthead import palette_problems
            for why in palette_problems(brand.active()) or []:
                be.REFUSED.append(("brand (configure-time masthead palette check)", "brand refused at configure",
                                   str(why)[:400]))
        except ImportError:
            pass
    import board_extras as bx
    import board_motion as bm
    work = Path(tempfile.mkdtemp(prefix="board-"))
    clips = out / "clips"
    manifest["labels"], manifest["errors"], manifest["sheets"] = {}, {}, {}
    manifest.setdefault("clips", {})
    pool = None
    if a.workers > 1 and rows & {2, 7, 9, 11, 12}:
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
    if surfaces_rec is not None:   # round 3: a Facebook slide the post never carries is tier 4 (never seen)
        unposted = extra("facebook pick for the tiers", lambda: fb_unposted(
            [q.stem for q in (out / "surfaces").glob("*__fb_carousel_slide_*.png")], surfaces_rec, dev), {})
        be.FB_UNPOSTED.update(n for v in unposted.values() for n in v)
        manifest["fb_unposted"] = unposted
    for rec in ((surfaces_rec or {}).get("wide_grounds") or {}).values():   # cycle 3: each 16:9 ground's pairing
        be.WIDE_GROUNDS[int(rec["index"])] = (rec["class"], rec["why"])
    for k in ("retired", "rerouted", "bridge_ladder", "wide_grounds"):   # cycle 3: production today, not as recorded
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
        # cycle 4 (carry-in 6): a surface drawn without a part is on its row even when it is not a hero case
        omitted = be.surface_omissions(surfaces_rec)
        heroes += [p for p in pick if p.stem in omitted and p not in heroes]
        # round 8 (AD E3, CD-20): every tile labelled through board_evidence.label, its pairing class first
        ladder = (surfaces_rec or {}).get("bridge_ladder") or {}

        def shipped(stem: str) -> str:   # cycle 3 (lane E): a bridge surface says which rung of the ladder shipped
            rec = ladder.get(stem)
            if not rec:
                return ""
            seam = rec.get("seam") or {}
            return (f"shipped variant: {rec.get('variant')} (composer's ladder) | seam {seam.get('mad_last')} of under "
                    f"{seam.get('gate_max')} | seam motion: {rec.get('motion_source', '-')}")
        def drawn_arch(stem: str):   # cycle 5: the archetype the tree drew (the run's, or today's where they differ)
            return bx.recipe_archetype(dev, stem, surfaces_rec)

        def note(stem: str) -> str:   # cycle 4: the ladder's rung and any recorded omission, on the tile's label
            diagram = "diagram slide" if r == 5 and drawn_arch(stem) == "diagram" else ""   # its tier is 2
            return " | ".join(x for x in (diagram, shipped(stem), be.omission_note(stem, surfaces_rec),
                                          be.record_note(stem, surfaces_rec)) if x)
        tiles = [(be.label(p.stem, note(p.stem)), Image.open(p).convert("RGB")) for p in heroes]
        sheets: dict[str, tuple[list, int | None, int]] = {}

        def add(item: dict, tiles=tiles) -> None:
            if item.get("image") is None:
                if item.get("refused"):   # round 8: the renderer refused this surface; its row says so
                    tiles.append((be.label(item["name"], "REFUSED"), be.refusal_tile(item["name"], item["refused"])))
                return
            withheld = be.masthead_note(item.get("masthead"))   # cycle 5: a withheld masthead is said on the tile
            if withheld:
                item = dict(item, label=f"{item['label']} | {withheld}")
            tiles.append((be.label(item["name"], item["label"]), item["image"]))
            manifest["labels"][item["name"]] = be.label(item["name"], item["label"])
            save_case(out, manifest, item["name"], item["image"])
            if item.get("masthead") is not None:   # cycle 5: a board-drawn social frame's own masthead record
                manifest.setdefault("masthead_records", {})[item["name"]] = dict(item["masthead"])
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
                arch = drawn_arch(p.stem)
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
                for sname, names in (extra("caption whole runs", lambda: row7_runs(out, tree, work, clips, manifest),
                                           {}) or {}).items():
                    manifest["sheets"][sname] = names
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
            for item in extra("callout held frame", lambda: callout_items(work, clips), []):
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
        case_files(out, manifest, manifest["rows"][str(r)])
        for n, _im in tiles:   # cycle 5: each case's production tier (scores are reported by tier)
            manifest.setdefault("case_tiers", {})[n.split(" | ")[0]] = be.tier(n.split(" | ")[0], n, WIDE_CAPTIONS)
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
        manifest["rows"]["13"] = row13_table(out, work, manifest.get("masthead_records"))
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
    for clip_name in manifest.get("clips") or {}:   # cycle 5: the clips' tiers too
        stem = Path(clip_name).stem
        manifest.setdefault("case_tiers", {})[f"clips/{stem}"] = be.tier(clip_tier_name(stem), "", WIDE_CAPTIONS)
    # cycle 5 (review item 6): every case of every row has a tier (rows 1, 2, 12, 13 and the peer feeds are assigned
    # outside the loop above), and every sheet saved whole says its tier on the sheet itself
    be.case_tiers(manifest, WIDE_CAPTIONS)
    manifest["tier_strips"] = extra("tier strips", lambda: tier_sheets(out), {})
    if manifest.get("not_for_panel"):   # round 3: after every sheet is drawn and tier-stamped
        manifest["not_evidence_marks"] = mark_not_evidence(out)
    manifest["tiers"] = {"what": be.TIER_WHAT, "rule": "lander ruling 2026-11-26 (SURFACE-USAGE-2026-11-26): a "
                         "lane's verdict is set by tiers 1-2", "drawn": "each tile's label strip (sheet()) and a "
                         "strip above each sheet saved whole (tier_sheets)", "counts": {
                             t: sum(1 for v in (manifest.get("case_tiers") or {}).values() if v == t)
                             for t in be.TIER_WHAT}}
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
    # cycle 5: a surface whose job has a code record (DEPS.json) takes the hash of what can change it: its job's
    # code key, the palette, the not-code environment and the inputs' union (equal across two boards exactly when
    # board_reuse.py may reuse it); every other file keeps the whole-tree hash
    per_job = 0
    if (out / "DEPS.json").is_file() and stamp.get("dirty_vs_head") is False:
        import board_deps
        deps_rec = json.loads((out / "DEPS.json").read_text(encoding="utf-8"))
        at_head = board_deps.Tree(tree, stamp["head"])
        job_keys = {j: board_deps.job_key(at_head, r)["sha256"] for j, r in deps_rec["jobs"].items()}
        manifest["job_code_keys"] = job_keys
        for rel in list(hashes):
            job = board_reuse.job_of(Path(rel).stem) if rel.startswith("surfaces/") else None
            if job in job_keys:
                hashes[rel] = "sha256:" + hashlib.sha256(json.dumps(
                    {"brand": a.brand, "env": reuse_env["sha256"], "inputs_sha256": manifest["inputs"]["sha256"],
                     "job": job, "code_key": job_keys[job], "output": rel}, sort_keys=True,
                    separators=(",", ":")).encode()).hexdigest()
                per_job += 1
    (out / "INPUT_HASHES.json").write_text(json.dumps(hashes, indent=1, sort_keys=True), encoding="utf-8",
                                           newline="\n")
    manifest["input_hashes"] = {"file": "INPUT_HASHES.json", "count": len(hashes), "per_job_code_key": per_job}
    (out / "board.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8", newline="\n")
    print("board:", out, manifest["src_sha256"])


if __name__ == "__main__":
    main()

