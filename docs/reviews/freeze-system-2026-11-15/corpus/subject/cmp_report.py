import json, sys
from collections import Counter
rows = json.load(open(sys.argv[1]))
win = [r for r in rows if r.get("kind")]
cov = [r for r in rows if not r.get("kind")]
ch = [r for r in win if r["base_box"] != r["tip_box"]]
print("window rows", len(win), "changed crops", len(ch))
for key in ("gt", "d"):
    worse = [r for r in win if r[f"{key}_base"] is not None and r[f"{key}_tip"] is not None and r[f"{key}_tip"] < r[f"{key}_base"] - 0.005]
    better = [r for r in win if r[f"{key}_base"] is not None and r[f"{key}_tip"] is not None and r[f"{key}_tip"] > r[f"{key}_base"] + 0.005]
    print(f"{key}: subject less visible {len(worse)}, more visible {len(better)}")
    for r in worse[:15]:
        print("   worse", r["photo"].split("\\")[-2][:14], r["photo"].split("\\")[-1][:12], r["surface"], r[f"{key}_base"], "->", r[f"{key}_tip"], r["tip_problems"])
fitc = Counter((r["base_fit"], r["tip_fit"]) for r in win)
print("fit base->tip", dict(fitc))
print("covers base->tip", Counter((r["base"], r["tip"]) for r in cov))
def prot_bad(p): return bool(p) and any(x.get("foreign") or x.get("line", 0) >= 0.6 or x.get("step", 0) >= 142 for x in p)
print("structure under type base", sum(prot_bad(r.get("base_prot")) for r in win), "tip", sum(prot_bad(r.get("tip_prot")) for r in win))
