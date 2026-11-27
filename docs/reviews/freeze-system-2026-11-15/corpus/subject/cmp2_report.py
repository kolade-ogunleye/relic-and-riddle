"""Two-base table: tip rules with a box vs (i) the crop without the box and (ii) the cycle-5 rules with the same box.
python cmp2_report.py c2_base.json c2_tip.json [label-filter: builder|reviewer|reviewed]"""
import json, sys
from collections import Counter
B = {(r["sha"], r["surface"]): r for r in json.load(open(sys.argv[1]))}
T = {(r["sha"], r["surface"]): r for r in json.load(open(sys.argv[2]))}
sets = {}
if len(sys.argv) > 3:
    sel = set(json.load(open(sys.argv[3]))) if sys.argv[3].endswith(".json") else None
else:
    sel = None
keys = [k for k in T if k in B and (sel is None or k[0] in sel)]
E = 1e-3


def less(x, y):
    return x is not None and y is not None and x < y - E


def bad(p):
    return bool(p) and any(q.get("foreign") or q.get("line", 0) >= 0.6 or q.get("step", 0) >= 142 for q in p)


out = Counter()
worst = []
for k in keys:
    b, t = B[k], T[k]
    out["rows"] += 1
    assert b["blind_box"] == t["blind_box"] or True
    for side, ref in (("vs_blind", "blind"), ("vs_c5", "box")):
        rv, rh = (b[f"{ref}_view"], b[f"{ref}_hold"])
        if less(t["box_view"], rv) or less(t["box_hold"], rh):
            out[f"less_{side}"] += 1
            worst.append((side, k[0][:8], t["photo"].split("\\")[-2][:14] + "/" + t["photo"].split("\\")[-1][:2],
                          k[1], (rv, rh), (t["box_view"], t["box_hold"])))
        if (t["box_view"] or 0) > (rv or 0) + E or (t["box_hold"] or 0) > (rh or 0) + E:
            out[f"more_{side}"] += 1
    if t["blind_fit"] and not t["box_fit"]:
        out["fit->unfit vs blind (picker may swap)"] += 1
        worst.append(("swap?", k[0][:8], t["photo"].split("\\")[-2][:14] + "/" + t["photo"].split("\\")[-1][:2], k[1],
                      t["blind_problems"], t["box_problems"]))
    if b["box_fit"] and not t["box_fit"]:
        out["fit->unfit vs c5-with-box"] += 1
    if not t["blind_fit"] and t["box_fit"]:
        out["unfit->fit vs blind"] += 1
    out["structures blind"] += bad(t["blind_prot"])
    out["structures c5-with-box"] += bad(b["box_prot"])
    out["structures tip"] += bad(t["box_prot"])
    out["new structure vs blind"] += bad(t["box_prot"]) and not bad(t["blind_prot"])
    out["new structure vs c5"] += bad(t["box_prot"]) and not bad(b["box_prot"])
for k in sorted(out):
    print(f"{k:45s} {out[k]}")
for w in worst[:40]:
    print("  ", w)
