"""PLATEFORM design stream (freeze cycle 6): the measures under every candidate tile, and the sheets (one per surface,
candidates side by side at half size, JPEG, the measures printed under each tile).

    python pf_measure.py <cards root> <stills root> <out dir> [--brand default|second|second-colours]
                         [--forms base,hug,...] [--subjects subjects.json]

<cards root>: pf_cards.py's output (<form>[__<brand>]/<kind>__<key>.png + records.json).
<stills root>: plates_draw.py's output per form (<form>/<brand>/<run>/{carousel,carousel_fb,stories}/*.jpg with their
still_layout.json). Every form is compared with "base" (integration 5) and with "none" (the same layout with no ground
at all: the photograph as the type finds it), both drawn by the same calls.

Measures (per tile):
- contrast: the lowest text element's ratio after a platform-like re-encode (JPEG q75, 4:2:0): the element's glyph
  pixels (within 40 levels of its fill) at their 10th percentile luminance against the 95th percentile of the ring
  3-10 px round them (every type fill's pixels dilated 3 px excluded: a gold run in a bone line is ink, not ground),
  WCAG ratio. The masthead is listed apart (no form changes it).
- dark: the share of the frame darker than base by > 20 luma (8-bit BT.709), outside the type boxes grown by 6 px;
  and "abs", the share darker than the bare photograph ("none") by > 20, beside base's own.
- edge: the darkening map D = luma(none) - luma(tile), outside the type boxes grown by 12 px: pixels where D steps by
  > 20 luma in one pixel (a hard edge). step = the 90th percentile of those steps; len = their count (px of edge);
  run = the longest straight run of them along one row or column (px); tex = the photograph's own median gradient
  (luma / px) within 8 px of them. slab = step / (tex + 4) x run / frame width: a long, straight, high-contrast edge
  on a smooth ground scores high (the reviewers' slab), a short edge on texture low. gap = the median distance (px)
  from those edge pixels to the nearest type box: an edge that follows the type (a plate's inset) is near, a band's
  frame-wide edge far.
- subject: where a labelled subject box exists, the share of its pixels (outside type) not darker than the bare
  photograph by > 20, for the tile and for base.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import binary_dilation

L709 = np.array([0.2126, 0.7152, 0.0722], np.float32)
TEXT_FILLS = ((243, 237, 226), (242, 182, 48), (184, 177, 163), (154, 147, 133))   # BONE, GOLD, STONE_LT, STONE
FORMS = ("none", "base", "hug", "block", "field", "grade", "split", "type")

# The reviewers' cases (REVIEW-plates-c5, REVIEW2/3, PANEL7 CD), by surface. cards: (kind, key); stills: (run, dir, file)
CASES = {
    "hook": [("card", "hook", "gobekli_tepe_07"), ("card", "hook", "tunguska_09"), ("card", "hook", "hero_obelisk"),
             ("card", "hook", "greek_fire_20")],
    "myth": [("card", "myth", "tunguska_09"), ("card", "myth", "trepanation_14"), ("card", "myth", "hero_richat")],
    "cover": [("still", "run_unfinished_obelisk_aswan_1792985912", "carousel", "carousel_slide_01.jpg"),
              ("still", "run_tunguska_event_1908_1792930600", "carousel_fb", "carousel_slide_01.jpg"),
              ("still", "run_nazca_lines_runway_paradox_1793267092", "carousel", "carousel_slide_01.jpg")],
    "carousel": [("still", "run_roman_self_healing_concrete_1793163918", "carousel", "carousel_slide_07.jpg"),
                 ("still", "run_unfinished_obelisk_aswan_1792985912", "carousel", "carousel_slide_04.jpg"),
                 ("still", "run_unfinished_obelisk_aswan_1792985912", "carousel", "carousel_slide_05.jpg"),
                 ("still", "run_unfinished_obelisk_aswan_1792985912", "carousel", "carousel_slide_07.jpg"),
                 ("still", "run_wieliczka_salt_mine_1792974672", "carousel", "carousel_slide_06.jpg"),
                 ("still", "run_richat_structure_sahara_eye_1793272675", "carousel", "carousel_slide_07.jpg")],
    "story": [("still", "run_greek_fire_siphon_1792735284_short", "stories", "story_01_hook.jpg"),
              ("still", "run_richat_structure_sahara_eye_1793272675", "stories", "story_01_hook.jpg"),
              ("still", "run_richat_structure_sahara_eye_1793272675", "stories", "story_02_reveal.jpg")],
}


def luma(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("RGB"), np.float32) @ L709


def _lin(a):
    a = a / 255.0
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def Y(a):
    return _lin(a) @ L709


def reencode(im: Image.Image) -> np.ndarray:
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=75, subsampling=2)
    b.seek(0)
    return np.asarray(Image.open(b).convert("RGB"), np.float32)


def element_contrast(a: np.ndarray, el: dict, fills=()) -> float | None:
    x0, y0, x1, y1 = (int(round(v)) for v in el["box"])
    r = a[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if r.size == 0:
        return None
    fill = el.get("fill")
    if isinstance(fill, (list, tuple)) and len(fill) >= 3:
        g = np.abs(r - np.array(fill[:3], np.float32)).max(-1) < 40
    else:
        g = Y(r) > 0.5
    if g.sum() < 30:
        return None
    yy = Y(r)
    ink = g.copy()
    for f in fills:   # every type fill on the frame is ink, never ground (a gold run inside a bone line, a strike)
        ink |= np.abs(r - np.array(f[:3], np.float32)).max(-1) < 40
    # the ground the eye reads a glyph against: a ring 3-10 px round the glyphs (not the whole box: a multi-line
    # element's box also holds photograph beside a short line, which no reader reads its glyphs against)
    bg = binary_dilation(g, iterations=10) & ~binary_dilation(ink, iterations=3)
    if bg.sum() < 30:
        return None
    lt, lb = np.percentile(yy[g], 10), np.percentile(yy[bg], 95)
    return float((max(lt, lb) + 0.05) / (min(lt, lb) + 0.05))


def type_mask(shape, els, grow: int) -> np.ndarray:
    m = np.zeros(shape, bool)
    H, W = shape
    for e in els:
        x0, y0, x1, y1 = (int(round(v)) for v in e["box"])
        m[max(0, y0 - grow):min(H, y1 + grow), max(0, x0 - grow):min(W, x1 + grow)] = True
    return m


def longest_run(b: np.ndarray) -> int:
    """The longest run of True along any row of `b`."""
    best = 0
    for row in b:
        if not row.any():
            continue
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        s, e = np.where(d == 1)[0], np.where(d == -1)[0]
        best = max(best, int((e - s).max()))
    return best


def edges(Lnone: np.ndarray, Lform: np.ndarray, tmask: np.ndarray, tbox: np.ndarray | None = None) -> dict:
    D = Lnone - Lform
    dy = np.abs(np.diff(D, axis=0))
    dx = np.abs(np.diff(D, axis=1))
    hy = (dy > 20) & ~tmask[1:] & ~tmask[:-1]
    hx = (dx > 20) & ~tmask[:, 1:] & ~tmask[:, :-1]
    n = int(hy.sum() + hx.sum())
    if n == 0:
        return {"step": 0.0, "len": 0, "run": 0, "tex": None, "slab": 0.0}
    steps = np.concatenate([dy[hy], dx[hx]])
    run = max(longest_run(hy), longest_run(hx.T))
    gy = np.abs(np.diff(Lnone, axis=0))[:, :-1]
    gx = np.abs(np.diff(Lnone, axis=1))[:-1, :]
    g = np.maximum(gy, gx)
    near = binary_dilation(hy[:, :-1] | hx[:-1, :], iterations=8) & ~(binary_dilation(hy[:, :-1] | hx[:-1, :],
                                                                                         iterations=2))
    tex = float(np.median(g[near])) if near.any() else 0.0
    step = float(np.percentile(steps, 90))
    W = Lnone.shape[1]
    gap = None
    if tbox is not None and tbox.any():
        from scipy.ndimage import distance_transform_edt
        dist = distance_transform_edt(~tbox)
        gap = round(float(np.median(np.concatenate([dist[1:][hy], dist[:, 1:][hx]]))))
    return {"step": round(step, 1), "len": n, "run": run, "tex": round(tex, 1),
            "slab": round(step / (tex + 4.0) * run / W, 2), "gap": gap}


def measure(form_im: Image.Image, base_im: Image.Image, none_im: Image.Image | None, els: list, subject=None) -> dict:
    Lf, Lb = luma(form_im), luma(base_im)
    t6 = type_mask(Lf.shape, els, 6)
    out: dict = {}
    if Lf.shape == Lb.shape:
        out["dark"] = round(float(((Lb - Lf > 20) & ~t6).mean()) * 100, 1)
    a = reencode(form_im)
    cs = []
    fills = {tuple(e["fill"][:3]) for e in els if isinstance(e.get("fill"), (list, tuple)) and len(e["fill"]) >= 3}
    fills = {f for f in fills if float(Y(np.array(f, np.float32))) > 0.2}   # an INK-type fill (the light-ground
    #                                       masthead) is the plates' own colour: never counted as another line's ink
    fills |= set(TEXT_FILLS) | {(c[2], c[1], c[0]) for c in TEXT_FILLS}   # runs in another fill (the test palettes
    #                                                                       swap red and blue: QF-35a second_brand)
    for e in els:
        c = element_contrast(a, e, fills)
        if c is not None:
            cs.append((round(c, 2), e.get("name", ""), (e.get("text") or "")[:24]))
    mast = [c for c in cs if c[1] in ("header_brand", "masthead")]
    body = [c for c in cs if c[1] not in ("header_brand", "masthead")]
    out["contrast"] = min(body) if body else None
    out["masthead"] = min(mast) if mast else None
    if none_im is not None and none_im.size == form_im.size:
        Ln = luma(none_im)
        out["abs"] = round(float(((Ln - Lf > 20) & ~t6).mean()) * 100, 1)
        out["edge"] = edges(Ln, Lf, type_mask(Lf.shape, els, 12), type_mask(Lf.shape, els, 0))
        if subject:
            x0, y0, x1, y1 = subject
            sl = (slice(y0, y1), slice(x0, x1))
            keep = ~t6[sl]
            if keep.any():
                out["subject"] = round(float(((Lf[sl] >= Ln[sl] - 20) & keep).sum() / keep.sum()) * 100, 1)
    return out


# ------------------------------------------------------------------------------------------------ sources
def card_source(root: Path, form: str, brand: str, kind: str, key: str):
    d = root / (form + ("" if brand == "default" else "__" + brand))
    p = d / f"{kind}__{key}.png"
    recs = json.loads((d / "records.json").read_text(encoding="utf-8")) if (d / "records.json").is_file() else {}
    r = recs.get(f"{kind}__{key}") or {}
    if not p.is_file():
        return None, r.get("refused", "not drawn"), []
    return Image.open(p).convert("RGB"), None, r.get("elements") or []


def still_source(root: Path, form: str, brand: str, run: str, sub: str, name: str):
    d = root / form / brand / run / sub
    p = d / name
    if not p.is_file():
        dj = root / form / brand / run / "draw.json"
        why = "not drawn"
        if dj.is_file():
            ks = json.loads(dj.read_text(encoding="utf-8")).get("kinds") or {}
            bad = [f"{k}: {v.get('error')}" for k, v in ks.items() if not v.get("ok")]
            why = "; ".join(bad)[:200] or why
        return None, why, []
    lay = json.loads((d / "still_layout.json").read_text(encoding="utf-8")).get(name) or {}
    els = [e for e in lay.get("elements") or [] if e.get("text") and e.get("box")]
    return Image.open(p).convert("RGB"), None, els


def source(args, form, case):
    if case[0] == "card":
        return card_source(args.cards, form, args.brand, case[1], case[2])
    return still_source(args.stills, form, args.brand, *case[1:])


def case_id(case) -> str:
    if case[0] == "card":
        return f"{case[1]}__{case[2]}"
    run = case[1][4:].rsplit("_", 1)[0] if not case[1].endswith("_short") else case[1][4:-6].rsplit("_", 1)[0]
    return f"{run[:22]}__{case[2]}__{Path(case[3]).stem.replace('carousel_slide_', 's').replace('story_', 'st')}"


# ------------------------------------------------------------------------------------------------ sheets
def font(px: int):
    for p in (Path(__file__).resolve().parents[4] / "fonts" / "IBMPlexMono-Medium.ttf",):
        if p.is_file():
            return ImageFont.truetype(str(p), px)
    return ImageFont.load_default()


def label(m: dict, refused: str | None) -> list[str]:
    if refused:
        return ["REFUSED / not drawn", refused[:60], refused[60:120]]
    c = m.get("contrast")
    e = m.get("edge") or {}
    lines = [f"min contrast {c[0]:.2f} ({c[1][:10]})" if c else "min contrast -",
             f"dark>base {m.get('dark', '-')}%  abs {m.get('abs', '-')}%",
             f"edge step {e.get('step', '-')} len {e.get('len', '-')} run {e.get('run', '-')}",
             f"tex {e.get('tex', '-')} gap {e.get('gap', '-')} slab {e.get('slab', '-')}"
             + (f"  subject {m['subject']}%" if m.get("subject") is not None else "")]
    if m.get("masthead"):
        lines.append(f"masthead {m['masthead'][0]:.2f}")
    return lines


def sheet(surface: str, rows: list, forms: list[str], out: Path) -> Path:
    """rows: [(case id, {form: (image | None, measures, refused)})]"""
    sample = next(im for _c, cells in rows for im, _m, _r in cells.values() if im is not None)
    tw, th = sample.width // 2, sample.height // 2
    lab_h, head_h, side = 118, 56, 16
    W = side + len(forms) * (tw + side)
    H = head_h + len(rows) * (th + lab_h + 34)
    S = Image.new("RGB", (W, H), (236, 233, 226))
    d = ImageDraw.Draw(S)
    f, fh = font(19), font(26)
    for j, fm in enumerate(forms):
        d.text((side + j * (tw + side), 14), fm + (" (photograph, no ground)" if fm == "none" else ""), fill=(20, 20, 20),
               font=fh)
    for i, (cid, cells) in enumerate(rows):
        y = head_h + i * (th + lab_h + 34)
        d.text((side, y), cid, fill=(90, 60, 20), font=f)
        for j, fm in enumerate(forms):
            x = side + j * (tw + side)
            im, m, refused = cells.get(fm, (None, {}, "not drawn"))
            if im is not None:
                S.paste(im.resize((tw, round(im.height * tw / im.width)), Image.Resampling.LANCZOS), (x, y + 26))
            else:
                d.rectangle([x, y + 26, x + tw, y + 26 + th], fill=(200, 196, 188))
            for k, ln in enumerate(label(m, refused)):
                d.text((x, y + 30 + th + k * 22), ln, fill=(20, 20, 20), font=f)
    p = out / f"sheet__{surface}.jpg"
    S.save(p, "JPEG", quality=86)
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cards", type=Path)
    ap.add_argument("stills", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--brand", default="default")
    ap.add_argument("--forms", default=",".join(FORMS))
    ap.add_argument("--surfaces", default=",".join(CASES))
    ap.add_argument("--subjects", type=Path)
    a = ap.parse_args()
    forms = a.forms.split(",")
    subjects = json.loads(a.subjects.read_text(encoding="utf-8")) if a.subjects and a.subjects.is_file() else {}
    out = a.out / a.brand
    out.mkdir(parents=True, exist_ok=True)
    table = {}
    for surface in a.surfaces.split(","):
        rows = []
        for case in CASES[surface]:
            cid = case_id(case)
            got = {fm: source(a, fm, case) for fm in set(forms) | {"base", "none"}}
            base_im, _r, base_els = got["base"]
            none_im = got["none"][0]
            if none_im is None and case[0] == "card":
                g = a.cards / "ground" / f"{case[1]}__{case[2]}.png"
                none_im = Image.open(g).convert("RGB") if g.is_file() else None
            cells = {}
            for fm in forms:
                im, refused, els = got[fm]
                if im is None or base_im is None:
                    cells[fm] = (im, {}, refused or "base not drawn")
                    continue
                m = measure(im, base_im, none_im if fm != "none" else None, els or base_els,
                            subjects.get(cid))
                if fm == "none":
                    m.pop("dark", None)
                cells[fm] = (im, m, None)
                table.setdefault(surface, {}).setdefault(cid, {})[fm] = m
            rows.append((cid, cells))
            print(surface, cid, {fm: (c[1].get("contrast"), c[1].get("dark"), c[1].get("abs"),
                                      (c[1].get("edge") or {}).get("slab"), c[1].get("subject"))
                                 for fm, c in cells.items()}, flush=True)
        if any(im is not None for _c, cells in rows for im, _m, _r in cells.values()):
            print("sheet", sheet(surface, rows, forms, out), flush=True)
    (out / "measures.json").write_text(json.dumps(table, indent=1, default=str), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
