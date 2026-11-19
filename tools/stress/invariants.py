"""The binding invariants, judged on one driven case (surfaces.Ctx), from the renderers' own records first and the
captured plates second. Each failure is {"cls", "detail"}; the classes:

EXCEPTION       an exception other than the one refusal type (refusal.LayoutError and its subclasses)
SILENT_NONE     the renderer returned nothing with no refusal and no warning
TEXT_DROP       a word of the input is drawn nowhere (no silent truncation, no dropped words), unless the renderer
                recorded leaving it out (shortened / dropped / withheld records)
GLYPH_MISSING   a drawn character the drawing face has no glyph for (and no recorded fallback): tofu
UNDER_FLOOR     a drawn text element under its feed-size floor (brand_system.TYPE_FLOOR via label_floor_px)
OUT_OF_SAFE     a text box outside the canvas or the surface's safe box / its recorded zone
COLLISION       two text boxes of one delivered frame overlap (both ways > COLLIDE_PX)
LOW_CONTRAST    a recorded or re-measured text ratio under its gate (4.5, masthead 5.5; marks 3.0)
LAYOUT_PROBLEM  the renderer recorded its own layout problem and delivered the surface anyway
FIGURE_GATE     src/figure_gate.check_run faults (a drawn figure that is not a signed value) or unrecorded text
LOGGED_DROP     a word of the input drawn nowhere, its leaving-out said only in a log line (no record carries it)
HARNESS         the harness itself failed on the case (never a product finding; fix the harness)
LINE_END        a caption burst (not the last) ending on a never-end word (brand_system.NEVER_END_WORDS)
LINE_END_BREAK  a line break inside a caption burst after a never-end word (the board counts these; the composer does not)
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any

COLLIDE_PX = 3            # an overlap both ways larger than this (glyph overhang of touching runs is not a collision)
SAFE_SLACK = 1            # px of rounding allowed at a safe-box edge
UNIT_WORDS = {"m", "km", "ft", "mi", "kg", "t", "lb", "lbs", "metres", "meters", "metre", "meter", "feet", "foot",
              "miles", "mile", "kilometres", "kilometers", "tonnes", "tons", "pounds", "kilograms", "cm", "mm",
              "bc", "bce", "ad", "ce", "c", "est", "about", "estimated", "more", "than", "between", "and", "nearly",
              "roughly", "over", "up", "to", "around"}


def _tokens(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", str(text or "")).casefold()
    t = t.replace("’", "'").replace("­", "")
    return [w for w in re.findall(r"[^\W_]+", t) if w]


def _word_tokens(text: str) -> list[str]:
    """The words of `text` that are not figures or units (those are the figure gate's: a renderer may draw a
    figure in the spoken unit or with its conversion)."""
    return [w for w in _tokens(text) if not any(ch.isdigit() for ch in w) and w not in UNIT_WORDS]


@lru_cache(maxsize=64)
def _cmap(path: str) -> frozenset:
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(path, lazy=True, fontNumber=0)
        cps = set()
        for t in f["cmap"].tables:
            cps.update(t.cmap.keys())
        return frozenset(cps)
    except Exception:  # noqa: BLE001
        return frozenset()


def _font_path(plate) -> str:
    return str(getattr(plate.font, "path", "") or "")


def _inside(box, zone, slack=SAFE_SLACK) -> bool:
    return (box[0] >= zone[0] - slack and box[1] >= zone[1] - slack and box[2] <= zone[2] + slack
            and box[3] <= zone[3] + slack)


def _overlap(a, b) -> tuple[int, int]:
    return min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])


def _ink_overlap(a, b) -> int | None:
    """Pixels where two plates' ink (each grown by 1 px) touch, on the canvas; None when a mask is missing."""
    import numpy as np
    if a.full is None or b.full is None:
        return None
    ma, mb = np.asarray(a.full) > 96, np.asarray(b.full) > 96
    (ax, ay), (bx, by) = a.origin, b.origin
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + ma.shape[1], bx + mb.shape[1]), min(ay + ma.shape[0], by + mb.shape[0])
    if x1 <= x0 or y1 <= y0:
        return 0
    sa = ma[y0 - ay:y1 - ay, x0 - ax:x1 - ax]
    sb = mb[y0 - by:y1 - by, x0 - bx:x1 - bx]
    import cv2
    grow = np.ones((3, 3), np.uint8)
    sa = cv2.dilate(sa.astype(np.uint8), grow) > 0
    return int((sa & sb).sum())


def check(ctx) -> list[dict[str, Any]]:
    import capture
    fails: list[dict[str, Any]] = []

    def fail(cls: str, detail: str) -> None:
        fails.append({"cls": cls, "detail": detail[:600]})

    if ctx.status == "error":
        err = str(ctx.error or "")
        fail("SILENT_NONE" if err.startswith("SILENT_NONE") else "HARNESS" if err.startswith("HARNESS") else
             "EXCEPTION", err)
        return fails
    if ctx.status == "refused":
        return fails

    # ------------------------------------------------ the plates actually on the delivered images
    images = [im for im in ctx.images if not im.get("derived")]
    # A captured plate counts as delivered when the renderer's own record holds it (its box inside a recorded
    # element's box, its text in that element's text): trial plates of a refit or another style are not. A surface
    # whose records carry no boxes (the end card) falls back to the pixels (capture.on_image).
    boxed = [r for r in ctx.records if r.get("box") and r.get("canvas")]

    def _norm(t) -> str:
        return " ".join(str(t or "").split()).casefold()

    def _near(a, b, slack=2) -> bool:
        return all(abs(x - y) <= slack for x, y in zip(a, b))

    # each record's own plates: the one plate drawn at exactly its box, else the plates of one size whose union is
    # its box (a block of lines); a plate drawn inside a record's box at another size or place was a trial
    owner: dict[int, dict] = {}
    for r in boxed:
        rt, rb = _norm(r.get("text")), r["box"]
        cands = [row for row in ctx.plates if tuple(row["canvas"]) == tuple(r["canvas"])
                 and all(row["box"][k] >= rb[k] - 2 for k in (0, 1)) and all(row["box"][k] <= rb[k] + 2 for k in (2, 3))
                 and (not rt or _norm(row["text"]) in rt)]
        exact = [row for row in cands if _near(row["box"], rb)]
        chosen = exact[:1]
        if not chosen:
            by_px: dict[float, list] = {}
            for row in cands:
                by_px.setdefault(row["px"], []).append(row)
            for group in by_px.values():
                u = [min(g["box"][0] for g in group), min(g["box"][1] for g in group),
                     max(g["box"][2] for g in group), max(g["box"][3] for g in group)]
                if _near(u, rb, 3):
                    chosen = group
                    break
        if len(chosen) > 1:
            # a block's lines drawn more than once (a trial break or place and the delivered one): per line row (its
            # ink top), the plate the delivered pixels show (its measured ratio highest; a trial reads about 1.0)
            im = next((i for i in images if i["name"] in str(r.get("surface", ""))
                       and tuple(i["im"].size) == tuple(r["canvas"])), None)
            best: dict[int, tuple] = {}
            for x in chosen:
                try:
                    v = x["_plate"].measure(im["im"]) if im is not None else None
                except Exception:  # noqa: BLE001
                    v = None
                # one line row: ink tops within a third of the line's height (a trial break's row sits a few px off)
                tol = max(4, (x["box"][3] - x["box"][1]) // 3)
                k = next((y for y in best if abs(y - int(x["box"][1])) <= tol), int(x["box"][1]))
                if k not in best or (v or 0) > (best[k][0] or 0):
                    best[k] = (v, x)
            chosen = [x for _v, x in best.values()]
        for row in chosen:
            owner.setdefault(id(row), r)

    def recorded(row):
        return owner.get(id(row))
    on: list[tuple[dict, dict]] = []
    for row in ctx.plates:
        same = [im for im in images if tuple(im["im"].size) == tuple(row["canvas"])]
        if not same:
            continue
        if any(tuple(r["canvas"]) == tuple(row["canvas"]) for r in boxed):
            r = recorded(row)
            if r is None:
                continue
            im = next((i for i in same if i["name"] in str(r.get("surface", ""))), same[0])
            on.append((row, im))
        else:
            im = next((i for i in same if i.get("any_frame") or capture.on_image(row, i["im"])), None)
            if im is not None:
                on.append((row, im))

    # ------------------------------------------------ text drawn whole
    drawn_texts = [str(r.get("text") or "") for r in ctx.records] + [r["text"] for r, _ in on] + \
        [ln["text"] for ln in ctx.lines_drawn]
    if not images:   # a clip whose last frame was not kept: every plate the renderer drew counts
        drawn_texts += [r["text"] for r in ctx.plates]
    pool: dict[str, int] = {}
    for t in drawn_texts:
        for w in _tokens(t):
            pool[w] = pool.get(w, 0) + 1
    drop_note = " ".join(ctx.recorded_drops).casefold()
    for fld, text in ctx.expect:
        need = _word_tokens(text)
        missing = [w for w in dict.fromkeys(need) if pool.get(w, 0) < need.count(w)]
        if not missing:
            continue
        if fld.casefold() in drop_note or any(w in drop_note for w in missing[:3]) or \
                any(k in drop_note for k in ("tagline_dropped", "headline_dropped", "headline_lines_dropped")):
            ctx.notes.append(f"{fld}: {missing[:6]} left out, recorded: {'; '.join(ctx.recorded_drops)[:200]}")
            continue
        logged = [w for w in getattr(ctx, "warnings", [])
                  if re.search(r"\b(dropp|left out|skipp|not drawn|omit|unlabell|not named|withh)", w)]
        if logged:   # left out with a log line only: no record a reviewer or the release gate reads says so
            fail("LOGGED_DROP", f"{fld}={text!r}: not drawn: {missing[:8]}; logged only: {logged[0][:200]}")
            continue
        fail("TEXT_DROP", f"{fld}={text!r}: not drawn: {missing[:8]}")

    # ------------------------------------------------ glyphs
    for row, im in on:
        p = row["_plate"]
        cps = _cmap(_font_path(p))
        if not cps:
            continue
        miss = sorted({ch for ch in str(p.text) if not ch.isspace() and ord(ch) not in cps
                       and unicodedata.category(ch)[0] not in ("C", "Z")})
        if miss:
            fail("GLYPH_MISSING", f"{im['name']}: {row['text']!r} in {row['face']} has no glyph for "
                                  f"{''.join(miss)[:20]!r} (fallback_for={row['fallback_for']})")

    # ------------------------------------------------ floors
    for row, im in on:
        floor = im.get("floor")
        if floor and row["px"] and row["px"] + 1e-6 < floor:
            fail("UNDER_FLOOR", f"{im['name']}: {row['text']!r} at {row['px']} px, floor {floor:.1f}")
    for r in ctx.records:
        if r.get("px") and r.get("canvas") and r.get("text"):
            from src.brand_system import label_floor_px
            w, h = r["canvas"]
            floor = label_floor_px(w, h, "credit")
            if float(r["px"]) + 1e-6 < floor:
                fail("UNDER_FLOOR", f"{r['surface']}: {r.get('name')} {r.get('text')!r} recorded at {r['px']} px, "
                                    f"floor {floor:.1f}")

    # ------------------------------------------------ safe boxes and canvas
    for r in ctx.records:
        box = r.get("box")
        if not box or not r.get("canvas"):
            continue
        w, h = r["canvas"]
        if not _inside(box, (0, 0, w, h), 0):
            fail("OUT_OF_SAFE", f"{r['surface']}: {r.get('name')} {box} runs past the {w}x{h} canvas")
        elif r.get("zone") and not _inside(box, r["zone"]):
            fail("OUT_OF_SAFE", f"{r['surface']}: {r.get('name')} {r.get('text')!r} {box} leaves its zone {r['zone']}")
    for row, im in on:
        box, safe = row["box"], im.get("safe")
        w, h = im["im"].size
        if not _inside(box, (0, 0, w, h), 0):
            fail("OUT_OF_SAFE", f"{im['name']}: {row['text']!r} {box} runs past the {w}x{h} canvas")
        elif safe and not _inside(box, safe):
            fail("OUT_OF_SAFE", f"{im['name']}: {row['text']!r} {box} leaves the safe box {list(safe)}")

    # ------------------------------------------------ collisions (text with text on one delivered frame)
    for im in images:
        rows = [r for r, i in on if i is im and r.get("alpha_max", 1.0) >= 0.99]
        for a_i, a in enumerate(rows):
            for b in rows[a_i + 1:]:
                if a["text"] == b["text"]:
                    continue
                ox, oy = _overlap(a["box"], b["box"])
                if ox > COLLIDE_PX and oy > COLLIDE_PX:
                    n = _ink_overlap(a["_plate"], b["_plate"])
                    if n is None or n > 0:   # the boxes overlap AND the ink does (an accent over a gap is not)
                        fail("COLLISION", f"{im['name']}: {a['text']!r} {a['box']} overlaps {b['text']!r} "
                                          f"{b['box']}" + (f" ({n} ink px touch)" if n is not None else ""))
    for p in ctx.problems:
        fail("LAYOUT_PROBLEM", p)

    # ------------------------------------------------ contrast
    from src.brand import TEXT_CONTRAST
    for c in ctx.contrast_rows:
        v, g = c.get("value"), c.get("gate")
        if g is None:
            continue
        if v is None:
            if c.get("text"):
                fail("LOW_CONTRAST", f"{c['surface']}: {c['name']} {c.get('text')!r}: no ratio recorded "
                                     f"(gate {g})")
            continue
        if float(v) + 1e-9 < float(g):
            how = ", ".join(f"{k} {c[k]:.2f}" for k in ("planned", "drawn", "graded")
                            if isinstance(c.get(k), (int, float)))
            fail("LOW_CONTRAST", f"{c['surface']}: {c['name']} {c.get('text')!r} {float(v):.2f} < {g}"
                                 + (f" ({how})" if how else ""))
    for r in ctx.records:
        v = r.get("contrast")
        if isinstance(v, (int, float)) and r.get("text"):
            g = 5.5 if "masthead" in str(r.get("name", "")).lower() else TEXT_CONTRAST
            if v + 1e-9 < g:
                fail("LOW_CONTRAST", f"{r['surface']}: {r.get('name')} {r.get('text')!r} recorded {v:.2f} < {g}")
    for row, im in on:
        if im["kind"] != "still":
            continue
        try:
            v = row["_plate"].measure(im["im"])
        except Exception:  # noqa: BLE001
            v = None
        if v is not None and v + 0.05 < TEXT_CONTRAST:   # re-measured on the delivered still (0.05 JPEG slack)
            fail("LOW_CONTRAST", f"{im['name']}: {row['text']!r} measures {v:.2f} on the delivered still")

    # ------------------------------------------------ figures
    if ctx.figure_gate:
        from src import figure_gate
        try:
            res = figure_gate.check_run(ctx.work, ctx.payload)
            for f in res["faults"]:
                fail("FIGURE_GATE", f)
            for u in res["unrecorded"]:
                fail("FIGURE_GATE", f"unrecorded: {u}")
        except Exception as e:  # noqa: BLE001
            fail("EXCEPTION", f"figure_gate.check_run raised {type(e).__name__}: {e}")

    # ------------------------------------------------ captions: the spoken sequence whole and in order; line ends
    exp_seq = getattr(ctx, "expect_sequence", None)
    if exp_seq is not None:
        exp, got = _tokens(exp_seq), _tokens(getattr(ctx, "drawn_sequence", ""))
        if exp != got:
            k = next((i for i, (a, b) in enumerate(zip(exp, got)) if a != b), min(len(exp), len(got)))
            fail("TEXT_DROP", f"captions: the bursts read {' '.join(got[max(0, k - 3):k + 5])!r} where the narration "
                              f"says {' '.join(exp[max(0, k - 3):k + 5])!r} (word {k}; {len(got)} of {len(exp)} words)")
        else:
            from src.brand_system import NEVER_END_WORDS
            spoken = str(exp_seq).split()
            pos = 0
            rows = [(ln, k == len(b) - 1, " ".join(b)) for _s, bursts in ctx.lines for b in bursts
                    for k, ln in enumerate(b)]
            recorded = {" ".join(str(x).split()) for x in getattr(ctx, "recorded_never_end", []) or []}
            for n, (ln, burst_end, burst) in enumerate(rows):
                pos += len(ln.split())
                if n == len(rows) - 1 or pos < 1 or pos > len(spoken):
                    continue
                src_word = spoken[pos - 1]
                bare = src_word.strip("\"'‘’“”").lower()
                if bare in NEVER_END_WORDS and src_word[-1] not in ".,;:!?—–)…":
                    if burst_end:   # the composer's own record (reading_rates never_end) counts burst ends
                        fail("LINE_END", f"captions: a burst ends on {src_word!r}: {burst!r}"
                                         + ("; recorded by the composer (reading_rates never_end)"
                                            if " ".join(burst.split()) in recorded else
                                            "; not in the composer's record"))
                    else:           # a line break inside a burst: only the board's evidence counts these
                        fail("LINE_END_BREAK", f"captions: a line inside a burst ends on {src_word!r}: {ln!r} "
                                               f"(burst {burst!r})")
    return fails


def classify(ctx, fails) -> str:
    if fails:
        return "fail"
    return "refused" if ctx.status == "refused" else "pass"
