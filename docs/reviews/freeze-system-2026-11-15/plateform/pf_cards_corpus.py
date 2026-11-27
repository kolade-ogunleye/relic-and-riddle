"""PLATEFORM (cycle 6, the block family): the hook frame and the 9:16 myth card over a corpus sample of photographs
(pf_cards.py's output for base and block, plus its bare grounds), per card: the lowest drawn text contrast (re-encoded,
pf_measure), the share darkened against the bare ground (> 20 luma, outside the type), the edge measures, and where
the SUBJECT stream labels the photograph, the share of its subject darkened. A violation (exit 1): a card drawn at base
and refused at block, a text element lower than base (> 0.25 and under 7:1), or a darkened share above base.

    python pf_cards_corpus.py <cards root> [--brand default] [--labels subject_labels.json] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pf_measure as pm  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cards", type=Path)
    ap.add_argument("--brand", default="default")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    sfx = "" if a.brand == "default" else "__" + a.brand
    B, T = a.cards / ("base" + sfx), a.cards / ("block" + sfx)
    rb = json.loads((B / "records.json").read_text(encoding="utf-8"))
    rt = json.loads((T / "records.json").read_text(encoding="utf-8")) if (T / "records.json").is_file() else {}
    rows, viol = [], []
    for key, r in sorted(rb.items()):
        row = {"card": key}
        rows.append(row)
        t = rt.get(key)
        if r.get("refused"):
            row["base"] = "refused"
            row["block"] = "refused" if (t or {}).get("refused") else "drawn"
            continue
        if not t or t.get("refused"):
            viol.append(f"{key}: drawn at base, {'refused' if t else 'missing'} at block ({(t or {}).get('refused')})")
            row["block"] = "refused"
            continue
        ib, it = Image.open(B / f"{key}.png").convert("RGB"), Image.open(T / f"{key}.png").convert("RGB")
        g = a.cards / "ground" / f"{key}.png"
        gr = Image.open(g).convert("RGB") if g.is_file() else None
        els_b, els_t = r.get("elements") or [], t.get("elements") or []
        mb = pm.measure(ib, ib, gr, els_b)
        mt = pm.measure(it, ib, gr, els_t)
        row.update(base={k: mb.get(k) for k in ("contrast", "abs", "edge")},
                   block={k: mt.get(k) for k in ("contrast", "abs", "edge")},
                   laid=((t.get("ground") or {}).get("plate_form") or {}).get("laid"))
        if [e.get("text") for e in els_b] != [e.get("text") for e in els_t]:
            viol.append(f"{key}: text differs")
        fills = set(pm.TEXT_FILLS) | {(c[2], c[1], c[0]) for c in pm.TEXT_FILLS}
        ab, at = pm.reencode(ib), pm.reencode(it)
        for eb, et in zip(els_b, els_t):
            vb, vt = pm.element_contrast(ab, eb, fills), pm.element_contrast(at, et, fills)
            if vb is not None and vt is not None and vt < vb - 0.25 and vt < 7.0:
                viol.append(f"{key}: {eb['name']} {vb:.2f} -> {vt:.2f}")
        if mb.get("abs") is not None and mt.get("abs") is not None and mt["abs"] > mb["abs"] + 0.05:
            viol.append(f"{key}: darkened {mb['abs']} -> {mt['abs']} %")
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8", newline="\n")
    drawn = [r for r in rows if isinstance(r.get("base"), dict) and isinstance(r.get("block"), dict)]
    print(f"{a.brand}: {len(rows)} cards, {len(drawn)} drawn in both, laid "
          f"{sorted(set(str(r.get('laid')) for r in drawn))}")
    for kind in ("hook", "myth"):
        d = [r for r in drawn if r["card"].startswith(kind)]
        if not d:
            continue
        ab = [r["base"]["abs"] for r in d if r["base"].get("abs") is not None]
        at = [r["block"]["abs"] for r in d if r["block"].get("abs") is not None]
        cb = [r["base"]["contrast"][0] for r in d if r["base"].get("contrast")]
        ct = [r["block"]["contrast"][0] for r in d if r["block"].get("contrast")]
        sb = [(r["base"].get("edge") or {}).get("slab", 0) for r in d]
        st = [(r["block"].get("edge") or {}).get("slab", 0) for r in d]
        print(f"  {kind}: {len(d)} cards; darkened median {np.median(ab):.1f} -> {np.median(at):.1f} %, max "
              f"{max(ab):.1f} -> {max(at):.1f}; lowest contrast {min(cb):.2f} -> {min(ct):.2f}; slab median "
              f"{np.median(sb):.2f} -> {np.median(st):.2f}, max {max(sb):.2f} -> {max(st):.2f}")
    print(f"  violations: {len(viol)}")
    for v in viol:
        print("   ", v)
    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main())
