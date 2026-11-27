"""THENNOW T2 (freeze cycle 6; panel 7 AD-T1 / CD-A1; review round 1 rulings 2026-11-27): the then-and-now layout over
every dev pair (unregistered: a stand-in, never a then-and-now) and every registered test pair (thennow_corpus:
each dev pair and each pool pair put through then_now.register), both formats, measured through the tree's own
layout calls (render() with the encode left out).

    python thennow_t2_layout.py <tree> <dev checkout> <cache> --palette <default|second|second-colours> --json <out>
    python thennow_t2_layout.py --merge <default.json> <second.json> <second-colours.json>

Per pair and format:
  refused  an unregistered pair: frame_pair records it "unregistered" with its reason (production's source() never
           returns one: it is a stand-in pairing)                                                     PASS if recorded
  window   a registered pair: the shared window at rest keeps the picture's central region (the central half of each
           axis, or all the centred least-zoom window holds of an axis it cannot hold half of)
  ink      the share of the frame's rows that are flat INK (luma std < 4 and mean < 30) at the opening and at rest,
           photographs as placed: at most 1 %
  place    each year plate: the ground's detail under it, on the photographs as placed (no divider, INK or plate),
           against each documented place: no place more than SIDE_MARGIN calmer
  contrast each plate's text: at or above contrast.high (7.0) after its tier (render()'s record; a tree without
           the tier record reports its planned contrast)
--merge: the same place for each plate in all three palettes (the choice reads the photographs only).
Exit 1 on any FAIL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thennow_corpus as tc  # noqa: E402

HIGH = 7.0


def layout(then_now, pair, w, h):
    """render() with the encode left out: the tree's whole layout and its record (then_now.last_meta)."""
    real = then_now._encode
    then_now._encode = lambda frames, out, n, width=0, height=0: out
    try:
        then_now.render(pair["old"], pair["old_year"], pair["new"], pair["new_year"], Path("unused.mp4"), w, h)
    finally:
        then_now._encode = real
    return json.loads(json.dumps(then_now.last_meta, default=str))


def flat_ink(frame) -> float:
    import numpy as np
    y = np.asarray(frame.convert("L"), np.float32)
    return float(((y.std(axis=1) < 4) & (y.mean(axis=1) < 30)).mean())


def central(then_now, size, big):
    """The central region the window must keep (restated here: the base tree has no such function)."""
    x0, y0, x1, y1 = then_now._cover_window(size, big)
    rest = tc.rest_view((x0, y0, x1, y1), size, then_now.PUSH)
    out = []
    for lo, hi in ((rest[0], rest[2]), (rest[1], rest[3])):
        out.append((lo, hi) if hi - lo < 2 * tc.CENTRE_HALF else (tc.CENTRE_HALF, 1 - tc.CENTRE_HALF))
    return out[0][0], out[1][0], out[0][1], out[1][1]


def main() -> int:
    if sys.argv[1] == "--merge":
        recs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in sys.argv[2:5]]
        by = [{(r["pair"], r["format"]): r for r in rs} for rs in recs]
        rows = []
        for k in by[0]:
            for which in ("then", "now"):
                got = [b[k]["place"][which]["chosen"] if k in b else None for b in by]
                ok = len(set(got)) == 1
                rows.append((k[0], k[1], f"place {which}", " / ".join(map(str, got)), "PASS" if ok else "FAIL"))
        import figures_corpus as fc
        print(fc.table(rows, ("pair", "fmt", "part", "default / second / second-colours", "verdict")))
        fails = sum(1 for r in rows if r[4] == "FAIL")
        print(f"\nTHENNOW T2 palettes: {len(rows)} rows, FAIL {fails}")
        return 1 if fails else 0
    tree, dev, cache = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    pal = sys.argv[sys.argv.index("--palette") + 1]
    then_now = tc.use_tree(tree)
    sys.path.insert(0, str(tree / "docs/reviews/freeze-system-2026-11-15"))
    import board
    board.configure_palette(tree, pal)
    rows, records = [], []
    cases = tc.pairs(tree, dev) + tc.registered_pairs(tree, dev, cache, then_now)
    for pair in cases:
        for w, h, tag in tc.FORMATS:
            size = (w, h)
            big = (round(w * (1 + then_now.PUSH)), round(h * (1 + then_now.PUSH)))
            a, b, info = then_now.frame_pair(pair["old"], pair["new"], w, h)
            rec = {"pair": pair["id"], "format": tag, "palette": pal, "registered": info.get("registered"),
                   "window": info.get("window"), "zoom": info.get("zoom")}
            if not pair.get("registered"):
                ok = info.get("registered") is False and "not registered" in str(info.get("unregistered", ""))
                rows.append((pair["id"], tag, "refused", "stand-in pairing, recorded unregistered" if ok else
                             "NOT recorded", "PASS" if ok else "FAIL"))
            else:
                ims = then_now._load_clean(pair["old"])
                t = info.get("trimmed_px", {}).get("old", [0, 0, 0, 0])
                sz = (ims.width - t[0] - t[2], ims.height - t[1] - t[3])
                win = then_now._cover_window(sz, big, float(info.get("zoom", 1.0)), float(info.get("v_centre", 0.5)),
                                             float(info.get("u_centre", 0.5)))
                view = tc.rest_view(win, sz, then_now.PUSH)
                r = central(then_now, sz, big)
                ok = view[0] <= r[0] + 1e-3 and view[1] <= r[1] + 1e-3 and view[2] >= r[2] - 1e-3 and view[3] >= r[3] - 1e-3
                rec["view"], rec["central"] = [round(v, 4) for v in view], [round(v, 4) for v in r]
                rows.append((pair["id"], tag, "window", f"{info.get('window')}: view {rec['view']} keeps "
                             f"{rec['central']}", "PASS" if ok else "FAIL"))
            split = then_now._split_at(w, h)
            rest = then_now.compose(then_now.pushed(a, then_now.CLIP_SECONDS, size),
                                    then_now.pushed(b, then_now.CLIP_SECONDS, size), split, divider=False)
            opening = then_now.compose(then_now.pushed(a, 0.0, size), then_now.pushed(b, 0.0, size), max(w, h),
                                       divider=False)
            ink = (flat_ink(opening), flat_ink(rest))
            rec["flat_ink"] = [round(v, 4) for v in ink]
            rows.append((pair["id"], tag, "ink", f"flat INK rows {ink[0]:.1%} at the opening, {ink[1]:.1%} at rest",
                         "PASS" if max(ink) <= 0.01 else "FAIL"))
            meta = layout(then_now, pair, w, h)
            side = meta.get("side") or {}
            cands = side.get("candidates") or list((side.get("edge_sum") or {"default": 0}).keys())
            words = meta["kicker"]
            years = {"then": pair["old_year"], "now": pair["new_year"]}
            rec["place"], rec["contrast"] = {}, {}
            for which in ("then", "now"):
                chosen = (side.get(which) or {}).get("side") or side.get("side") or cands[0]

                def at(c, which=which):
                    """The plate's place at documented position `c`, as render() reads it: the marker's two lines'
                    ink boxes (kicker and year, TextPlate), their union PAPER_PAD px wider, kept to its panel."""
                    k, f, anchor = then_now.marker_lines(which, years[which], w, h, words[which], c)
                    key = "landscape" if w > h else "portrait"
                    bx = [then_now.TextPlate(t, font, then_now.brand.BONE, xy, anchor, tr, 0, then_now.brand.INK, 0,
                                             False, size).box
                          for t, font, xy, tr in ((words[which], then_now.kicker_font(key), k,
                                                   then_now.kicker_track(key)),
                                                  (str(years[which]), then_now.year_font(key), f, 0))]
                    clip = then_now.panel_box(which, w, h)
                    pad = then_now.PAPER_PAD
                    return (max(clip[0], min(b[0] for b in bx) - pad), max(clip[1], min(b[1] for b in bx) - pad),
                            min(clip[2], max(b[2] for b in bx) + pad), min(clip[3], max(b[3] for b in bx) + pad))

                def d(bx, which=which):
                    vals = [tc.detail(rest, bx)] + ([tc.detail(opening, bx)] if which == "then" else [])
                    return sum(vals) / len(vals)
                per = {c: round(d(at(c)), 3) for c in cands}
                got = per[chosen] if chosen in per else round(d(at(chosen)), 3)
                calm = min(per.values())
                ok = got <= calm / (1 - tc.SIDE_MARGIN) + 1e-6
                tier = (side.get(which) or {}).get("tier") or {}
                c = tier.get("contrast")
                if c is None:
                    pc = meta.get("planned_contrast") or {}
                    c = min(pc.get(f"{which}_kicker", 0.0), pc.get(f"{which}_year", 0.0))
                rec["place"][which] = {"chosen": chosen, "detail": got, "candidates": per}
                rec["contrast"][which] = {"tier": tier.get("tier"), "contrast": c}
                rows.append((pair["id"], tag, f"place {which}", f"{chosen}: detail {got:.2f}, calmest documented "
                             f"{calm:.2f} ({min(per, key=per.get)})", "PASS" if ok else "FAIL"))
                rows.append((pair["id"], tag, f"contrast {which}", f"{tier.get('tier', 'planned')}: {c:.2f}",
                             "PASS" if c + 1e-9 >= HIGH else "FAIL"))
            records.append(rec)
            print(f"measured {pair['id']} {tag} {pal}", file=sys.stderr, flush=True)
    import figures_corpus as fc
    print(fc.table(rows, ("pair", "fmt", "part", "measure", "verdict")))
    fails = sum(1 for r in rows if r[4] == "FAIL")
    print(f"\nTHENNOW T2 ({pal}): {len(rows)} rows, FAIL {fails}")
    Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(records, indent=1), encoding="utf-8",
                                                            newline="\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
