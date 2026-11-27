"""PLATEFORM: per text element, the re-encoded contrast under each form against base on the same still (matched by
element name and occurrence), the count lower than base by > 0.25 and the worst drop.

    python pf_elements.py <cards root> <stills root> <brand> <form>[,<form>...]
"""
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pf_measure as m  # noqa: E402


def contrasts(im, els):
    a = m.reencode(im)
    fills = {tuple(e["fill"][:3]) for e in els if isinstance(e.get("fill"), (list, tuple)) and len(e["fill"]) >= 3}
    fills = {f for f in fills if float(m.Y(m.np.array(f, m.np.float32))) > 0.2} | set(m.TEXT_FILLS) \
        | {(c[2], c[1], c[0]) for c in m.TEXT_FILLS}
    c = collections.Counter()
    out = {}
    for e in els:
        k = (e.get("name"), c[e.get("name")])
        c[e.get("name")] += 1
        out[k] = (m.element_contrast(a, e, fills), (e.get("text") or "")[:28])
    return out


def main():
    class A:
        pass
    a = A()
    a.cards, a.stills, a.brand = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    for form in sys.argv[4].split(","):
        lower, n, worst = [], 0, None
        under = {"base7": 0, "form7": 0, "base48": 0, "form48": 0}
        for surface, cases in m.CASES.items():
            for case in cases:
                bi, _r, be = m.source(a, "base", case)
                fi, _r2, fe = m.source(a, form, case)
                if bi is None or fi is None:
                    continue
                cb, cf = contrasts(bi, be), contrasts(fi, fe)
                for k, (vb, t) in cb.items():
                    vf = (cf.get(k) or (None,))[0]
                    if vb is None or vf is None or k[0] in ("header_brand", "masthead"):
                        continue
                    n += 1
                    under["base7"] += vb < 7.0
                    under["form7"] += vf < 7.0
                    under["base48"] += vb < 4.8
                    under["form48"] += vf < 4.8
                    if vf < vb - 0.25 and vf < 7.0:
                        lower.append((round(vf - vb, 2), round(vb, 2), round(vf, 2), m.case_id(case), k[0], t))
        lower.sort()
        print(f"{a.brand} {form}: {n} elements, {len(lower)} lower than base by > 0.25 (and under 7:1); under 7:1 "
              f"base {under['base7']} -> {under['form7']}; under 4.8 base {under['base48']} -> {under['form48']}")
        for r in lower[:12]:
            print("   ", r)


if __name__ == "__main__":
    main()
