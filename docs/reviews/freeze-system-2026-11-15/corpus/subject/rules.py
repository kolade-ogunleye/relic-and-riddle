"""Search a small monotone rule family for zero cuts on the labelled set; leave-one-topic-out check."""
import json, itertools, sys
from evalboxes import *
rows = json.load(open(HERE / "feats.json"))
m0 = sys.argv[1]
def topic(r): return r["name"].split("/")[0]
def feats(r):
    f = r[m0]
    return dict(agree=r["agree"], soft=f.get("soft", 1), area=r["area"], edges=r["edges"], ncomp=f.get("ncomp", 9))
GRID = dict(agree=[0.6, 0.7, 0.8, 0.9, 0.95], soft=[0.05, 0.1, 0.15, 0.2, 0.3], amin=[0.02, 0.05, 0.1, 0.2],
            amax=[0.5, 0.6, 0.7, 0.8, 0.9], edges=[0, 1, 2])
def acc(r, p):
    f = feats(r)
    return f["agree"] >= p[0] and f["soft"] <= p[1] and p[2] <= f["area"] <= p[3] and f["edges"] <= p[4]
def score(rs, p):
    a = [r for r in rs if acc(r, p)]
    return sum(1 for r in a if r["cov"] < 0.85), len(a)
best = []
for p in itertools.product(*GRID.values()):
    cuts, n = score(rows, p)
    if cuts == 0: best.append((n, p))
best.sort(reverse=True)
print("zero-cut rules, most coverage:", best[:5])
# leave one topic out: choose on the rest (zero cuts, max coverage, ties -> strictest), test on the held-out topic
tot_cut = tot_acc = 0
for t in sorted({topic(r) for r in rows}):
    train = [r for r in rows if topic(r) != t]; test = [r for r in rows if topic(r) == t]
    cand = []
    for p in itertools.product(*GRID.values()):
        c, n = score(train, p)
        if c == 0: cand.append((n, p))
    if not cand: continue
    n, p = max(cand)
    c, k = score(test, p); tot_cut += c; tot_acc += k
    if c: print("held-out", t, "cuts", c, "of", k, p)
print(f"LOTO: accepted {tot_acc} of {len(rows)}, cuts {tot_cut}")
