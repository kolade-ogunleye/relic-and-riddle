"""PLATEFORM: the comparison table (markdown) from pf_measure.py's measures.json, one row per case and form, and
a per-form summary over all cases: worst contrast, median and max abs darkening, max slab, subject share.

    python pf_table.py <measures.json> [...]
"""
import json
import sys
from statistics import median

FORMS = ("base", "hug", "block", "field", "grade", "split", "type")


def main():
    rows, agg = [], {f: {"c": [], "abs": [], "absb": [], "slab": [], "subj": [], "dark": []} for f in FORMS}
    for p in sys.argv[1:]:
        t = json.load(open(p, encoding="utf-8"))
        for surface, cases in t.items():
            for cid, forms in cases.items():
                b = forms.get("base") or {}
                for f in FORMS:
                    m = forms.get(f)
                    if not m:
                        continue
                    c = (m.get("contrast") or [None])[0]
                    e = m.get("edge") or {}
                    rows.append(f"| {surface} | {cid} | {f} | {c if c is not None else '-'} | {m.get('dark', '-')} | "
                                f"{m.get('abs', '-')} | {e.get('step', '-')} | {e.get('run', '-')} | {e.get('tex', '-')} | "
                                f"{e.get('gap', '-')} | {e.get('slab', '-')} | {m.get('subject', '-')} |")
                    a = agg[f]
                    if c is not None:
                        a["c"].append((c, surface, cid))
                    if m.get("abs") is not None:
                        a["abs"].append(m["abs"])
                        if b.get("abs") is not None:
                            a["absb"].append(m["abs"] - b["abs"])
                    if e.get("slab") is not None:
                        a["slab"].append(e["slab"])
                    if m.get("subject") is not None and b.get("subject") is not None:
                        a["subj"].append(m["subject"] - b["subject"])
                    if m.get("dark") is not None:
                        a["dark"].append(m["dark"])
    print("| surface | case | form | min contrast (re-encoded) | dark > base % | abs % | edge step | run px | tex | gap px | slab | subject % |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    print()
    print("| form | tiles | worst contrast (case) | cases under 4.5 | median abs % | max abs % | max (abs - base abs) | median slab | max slab | subject vs base (min) |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for f in FORMS:
        a = agg[f]
        if not a["abs"] and not a["c"]:
            continue
        w = min(a["c"]) if a["c"] else None
        print(f"| {f} | {len(a['c'])} | {w[0] if w else '-'} ({w[2] if w else ''}) | {sum(1 for c in a['c'] if c[0] < 4.5)} | "
              f"{round(median(a['abs']), 1) if a['abs'] else '-'} | {max(a['abs']) if a['abs'] else '-'} | "
              f"{round(max(a['absb']), 1) if a['absb'] else '-'} | {round(median(a['slab']), 2) if a['slab'] else '-'} | "
              f"{max(a['slab']) if a['slab'] else '-'} | {round(min(a['subj']), 1) if a['subj'] else '-'} |")


if __name__ == "__main__":
    main()
