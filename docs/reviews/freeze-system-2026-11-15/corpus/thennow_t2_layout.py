"""THENNOW T2 (freeze cycle 6; panel 7 AD-T1 / CD-A1): the then-and-now panels' crops and the year plates' places,
over every dev pair in both formats (thennow_corpus.pairs), measured through the tree's own layout calls (no encode).

    python thennow_t2_layout.py <tree> <dev checkout> [--json out.json]

Per pair and format:
  crop   9:16, each panel: the part of its photograph the frame shows at rest (the tree's own record, or the shared
         window frame_pair recorded). PASS when it holds the photograph's central half on both axes and at least
         the share of its height the 16:9 frame shows of it, or when the tree records the panel's designed
         alternative ("contained": the photograph whole in its panel). 16:9 is the reference layout: reported only.
  place  each year plate (its drawn group box, render()'s record, run with the encode left out): the ground's detail
         under it on the split at rest (THEN: averaged with the frame it opens on) against the same box at each of
         the tree's documented places for it. PASS when no documented place is more than SIDE_MARGIN calmer.
Exit 1 on any FAIL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thennow_corpus as tc  # noqa: E402


def layout(then_now, pair, w, h):
    """render() with the encode left out: the tree's whole layout and its record (then_now.last_meta)."""
    real = then_now._encode
    then_now._encode = lambda frames, out, n, width=0, height=0: out
    try:
        then_now.render(pair["old"], pair["old_year"], pair["new"], pair["new_year"], Path("unused.mp4"), w, h)
    finally:
        then_now._encode = real
    return json.loads(json.dumps(then_now.last_meta, default=str))


def main() -> int:
    tree, dev = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    then_now = tc.use_tree(tree)
    rows, records = [], []
    for pair in tc.pairs(tree, dev):
        info16 = then_now.frame_pair(pair["old"], pair["new"], 1920, 1080)[2]
        sizes = tc.trimmed_sizes(then_now, pair, info16)
        ref = tc.shown_boxes(then_now, info16, sizes, 1920, 1080)
        for w, h, tag in tc.FORMATS:
            size = (w, h)
            a, b, info = then_now.frame_pair(pair["old"], pair["new"], w, h)
            shown = ref if tag == "169" else tc.shown_boxes(then_now, info, sizes, w, h)
            rec = {"pair": pair["id"], "format": tag, "registered": info.get("registered"),
                   "layout": info.get("layout", info.get("window")), "shown": shown, "ref169": ref}
            for which in ("then", "now"):
                s = shown[which]
                if tag == "169":
                    rows.append((pair["id"], tag, f"crop {which}", f"{s['w']:.2f} w x {s['h']:.2f} h, centre half "
                                 f"{s['centre_half']} (reference layout)", "-"))
                    continue
                ok = s.get("mode") == "contained" or (s["centre_half"] and s["h"] >= ref[which]["h"] - 0.005)
                rows.append((pair["id"], tag, f"crop {which}",
                             f"{s.get('mode')}: {s['w']:.2f} w x {s['h']:.2f} h of it (16:9 shows {ref[which]['h']:.2f}"
                             f" h), box {s['box']}, centre half {s['centre_half']}", "PASS" if ok else "FAIL"))
            meta = layout(then_now, pair, w, h)
            split = then_now._split_at(w, h)
            rest = then_now.compose(then_now.pushed(a, then_now.CLIP_SECONDS, size),
                                    then_now.pushed(b, then_now.CLIP_SECONDS, size), split)
            opening = then_now.compose(then_now.pushed(a, 0.0, size), then_now.pushed(b, 0.0, size), max(w, h))
            side = meta.get("side") or {}
            cands = side.get("candidates") or list((side.get("edge_sum") or {"default": 0}).keys())
            words = meta["kicker"]
            years = {"then": pair["old_year"], "now": pair["new_year"]}
            rec["place"] = {}
            for which in ("then", "now"):
                chosen = (side.get(which) or {}).get("side") or side.get("side") or cands[0]

                def at(c):
                    """The plate's place at documented position `c`, as render() reads it: the marker's two lines'
                    ink boxes (kicker and year, TextPlate), their union PAPER_PAD px wider, kept to its panel."""
                    k, f, anchor = then_now.marker_lines(which, years[which], w, h, words[which], c)
                    key = "landscape" if w > h else "portrait"
                    bx = [then_now.TextPlate(t, font, then_now.brand.BONE, xy, anchor, tr, 0, then_now.brand.INK, 0,
                                             False, size).box
                          for t, font, xy, tr in ((words[which], then_now.kicker_font(key), k, then_now.kicker_track(key)),
                                                  (str(years[which]), then_now.year_font(key), f, 0))]
                    clip = then_now.panel_box(which, w, h)
                    pad = then_now.PAPER_PAD
                    return (max(clip[0], min(b[0] for b in bx) - pad), max(clip[1], min(b[1] for b in bx) - pad),
                            min(clip[2], max(b[2] for b in bx) + pad), min(clip[3], max(b[3] for b in bx) + pad))

                def d(bx):
                    vals = [tc.detail(rest, bx)] + ([tc.detail(opening, bx)] if which == "then" else [])
                    return sum(vals) / len(vals)
                per = {c: round(d(at(c)), 3) for c in cands}
                got = per[chosen] if chosen in per else round(d(at(chosen)), 3)
                calm = min(per.values())
                ok = got <= calm / (1 - tc.SIDE_MARGIN) + 1e-6
                rec["place"][which] = {"chosen": chosen, "detail": got, "candidates": per,
                                       "drawn_box": meta["groups"][which]}
                rows.append((pair["id"], tag, f"place {which}", f"{chosen}: detail {got:.2f}, calmest documented "
                             f"{calm:.2f} ({min(per, key=per.get)})", "PASS" if ok else "FAIL"))
            records.append(rec)
            print(f"measured {pair['id']} {tag}", file=sys.stderr, flush=True)
    import figures_corpus as fc
    print(fc.table(rows, ("pair", "fmt", "part", "measure", "verdict")))
    fails = sum(1 for r in rows if r[4] == "FAIL")
    print(f"\nTHENNOW T2: {sum(1 for r in rows if r[4] != '-')} gated rows, FAIL {fails}")
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(records, indent=1), encoding="utf-8",
                                                                newline="\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
